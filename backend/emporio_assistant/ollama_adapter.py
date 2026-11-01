"""Local Ollama adapter (EMP-11) for qwen3:8b, through the native /api/chat endpoint.

Real local inference, not a simulation and not a paid call: no ledger operation is written
(API cost 0, operational cost not measured). Checked 2026-11-01 against docs.ollama.com
(capabilities/tool-calling, capabilities/thinking, api/chat, api/ps):
- tools are {type: function, function: {name, description, parameters}}; the reply carries
  `message.tool_calls[].function.{name, arguments}` with no call id, and each result goes back
  as a {role: tool, tool_name, content} message, in call order;
- `think` is set explicitly (false); `options.num_ctx` / `options.num_predict` are explicit;
- responses report load/prompt/eval durations (ns) and token counts; /api/ps reports the
  allocated `context_length` and `size_vram`.
The model-declared context window is not the allocated one, and Ollama documents no error when
a prompt exceeds `num_ctx`. So the prompt is bounded before sending (bytes >= tokens for a
byte-level tokenizer) and refused when bound + num_predict > num_ctx: never cut silently.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from .generation import (
    END_TURN,
    MAX_TOKENS,
    OTHER,
    TOOL_USE,
    AdapterConfigError,
    CallContext,
    ModelReply,
    PromptTooLarge,
    ProviderError,
    ToolCall,
    byte_bound,
)

MODEL = "qwen3:8b"
EXPECTED_DIGEST_PREFIX = "500a1f067a9f"  # `ollama list` id of the installed qwen3:8b Q4_K_M
DEFAULT_URL = "http://127.0.0.1:11434"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
REQUEST_OVERHEAD_TOKENS = 1_000  # chat template tokens and tool rendering
_ONE_AT_A_TIME = threading.Lock()  # one local inference per process


@dataclass(frozen=True)
class OllamaSettings:
    model: str = MODEL
    base_url: str = DEFAULT_URL
    think: bool = False  # starting hypothesis for this hardware (6 GB VRAM)
    num_ctx: int = 32_768  # requested allocation; the real one is read from /api/ps
    num_predict: int = 2_048
    call_timeout_s: float = 300.0  # local CPU/GPU offload can be slow
    keep_alive: str = "10m"
    expected_digest_prefix: str = EXPECTED_DIGEST_PREFIX

    def validate(self) -> None:
        url = urlsplit(self.base_url)
        if url.scheme != "http" or url.hostname not in LOOPBACK:
            raise AdapterConfigError("the Ollama URL must be http on a loopback address")
        if not 2_048 <= self.num_ctx <= 40_960:
            raise AdapterConfigError("num_ctx must be between 2048 and 40960 (model maximum)")
        if not 1 <= self.num_predict < self.num_ctx:
            raise AdapterConfigError("num_predict must be positive and below num_ctx")
        if not 1 <= self.call_timeout_s <= 900:
            raise AdapterConfigError("call_timeout_s must be between 1 and 900 seconds")
        if type(self.think) is not bool:
            raise AdapterConfigError("think must be true or false")


class Transport(Protocol):
    def __call__(self, method: str, path: str, body: dict | None, timeout: float) -> dict: ...


def http_transport(base_url: str) -> Transport:
    def send(method: str, path: str, body: dict | None, timeout: float) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    return send


def to_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "tool_results":
            out += [  # no call id in Ollama: results follow the call order
                {"role": "tool", "tool_name": r.name, "content": r.content} for r in m["results"]
            ]
        elif "tool_calls" in m:
            message = m["state"]
            if message is None:  # not produced by this adapter: rebuild the calls only
                message = {
                    "role": "assistant",
                    "content": m["text"],
                    "tool_calls": [
                        {"function": {"name": c.name, "arguments": c.arguments}}
                        for c in m["tool_calls"]
                    ],
                }
            out.append(message)
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def to_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _ms(value: Any) -> int | None:
    return round(value / 1_000_000) if type(value) is int else None


def _metrics(body: dict) -> dict:
    """Counters and durations exactly as reported (ns -> ms); absent ones stay absent."""
    out = {
        "model": body.get("model"),
        "done_reason": body.get("done_reason"),
        "prompt_eval_count": body.get("prompt_eval_count"),
        "eval_count": body.get("eval_count"),
        "load_ms": _ms(body.get("load_duration")),  # cold load is reported here, kept apart
        "prompt_eval_ms": _ms(body.get("prompt_eval_duration")),
        "eval_ms": _ms(body.get("eval_duration")),
        "total_ms": _ms(body.get("total_duration")),
    }
    return {k: v for k, v in out.items() if v is not None}


def from_body(body: dict, model: str, call: int) -> ModelReply:
    if body.get("model") not in (None, model):
        raise ProviderError("model_mismatch", f"Ollama answered with {body.get('model')!r}")
    message = body.get("message") or {}
    calls = []
    for index, item in enumerate(message.get("tool_calls") or []):
        function = item.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass  # left as text: argument validation rejects it
        # Ollama returns no call id: one is made per call and result order is preserved.
        calls.append(ToolCall(f"ollama_{call}_{index}", str(function.get("name")), arguments))
    reason = body.get("done_reason")
    if not body.get("done"):
        stop = OTHER
    elif reason == "length":
        stop = MAX_TOKENS
    elif reason == "stop":
        stop = TOOL_USE if calls else END_TURN
    else:
        stop = OTHER
    state = {k: v for k, v in message.items() if k in ("role", "content", "tool_calls", "thinking")}
    return ModelReply(
        str(message.get("content") or ""),
        calls,
        stop,
        state,
        raw_stop=reason,
        metrics=_metrics(body),
    )


class OllamaGenerator:
    mode = "local"

    def __init__(self, settings: OllamaSettings = OllamaSettings(), transport: Any = None):
        settings.validate()
        self.settings = settings
        self.transport = transport if transport is not None else http_transport(settings.base_url)
        self._calls = 0

    def describe(self) -> dict:
        s = self.settings
        return {
            "provider": "ollama",
            "model": s.model,
            "think": s.think,
            "num_ctx": s.num_ctx,
            "num_predict": s.num_predict,
            "call_timeout_s": s.call_timeout_s,
            "keep_alive": s.keep_alive,
            "base_url": s.base_url,
            "sdk_retries": 0,
        }

    def _get(self, path: str, body: dict | None = None, method: str = "GET") -> dict:
        try:
            return self.transport(method, path, body, 10.0)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise ProviderError("local_model_unavailable", type(error).__name__) from None

    def runtime(self) -> dict:
        """Server version, installed model identity and what is loaded now (`ollama ps`)."""
        version = self._get("/api/version").get("version")
        tags = self._get("/api/tags").get("models") or []
        installed = next((m for m in tags if m.get("name") == self.settings.model), None)
        loaded = [
            {
                k: m.get(k)
                for k in ("name", "digest", "size", "size_vram", "context_length", "expires_at")
            }
            for m in self._get("/api/ps").get("models") or []
        ]
        for m in loaded:  # share of the loaded model that is in GPU memory
            if type(m["size"]) is int and type(m["size_vram"]) is int and m["size"]:
                m["gpu_share"] = round(m["size_vram"] / m["size"], 3)
        return {
            "ollama_version": version,
            "model": self.settings.model,
            "installed": None
            if installed is None
            else {
                "digest": installed.get("digest"),
                "size": installed.get("size"),
                "details": installed.get("details"),
            },
            "loaded": loaded,
        }

    def check_model(self) -> dict:
        """Refuse a missing model or a different build; never substitute another model."""
        info = self.runtime()
        installed = info["installed"]
        if installed is None:
            raise AdapterConfigError(f"{self.settings.model} is not installed in Ollama")
        digest = str(installed.get("digest") or "")
        if not digest.startswith(self.settings.expected_digest_prefix):
            raise AdapterConfigError(
                f"{self.settings.model} digest {digest[:12]!r} is not the expected build "
                f"{self.settings.expected_digest_prefix!r}"
            )
        return info

    def create(
        self, *, system: str, messages: list[dict], tools: list[dict], context: CallContext
    ) -> ModelReply:
        s = self.settings
        payload_messages = to_messages(system, messages)
        functions = to_tools(tools)
        bound = byte_bound(payload_messages, functions) + REQUEST_OVERHEAD_TOKENS
        if bound + s.num_predict > s.num_ctx:
            raise PromptTooLarge(f"prompt bound {bound} + {s.num_predict} > num_ctx {s.num_ctx}")
        body = {
            "model": s.model,
            "messages": payload_messages,
            "tools": functions,
            "think": s.think,
            "stream": False,
            "keep_alive": s.keep_alive,
            "options": {"num_ctx": s.num_ctx, "num_predict": s.num_predict},
        }
        timeout = max(1.0, min(s.call_timeout_s, context.timeout_s))
        if not _ONE_AT_A_TIME.acquire(timeout=timeout):
            raise ProviderError("local_model_busy", "another local inference is running")
        try:
            self._calls += 1
            try:
                reply = self.transport("POST", "/api/chat", body, timeout)
            except TimeoutError:
                raise ProviderError("local_model_timeout") from None
            except (urllib.error.URLError, OSError, ValueError) as error:
                raise ProviderError("local_model_error", type(error).__name__) from None
        finally:
            _ONE_AT_A_TIME.release()
        if not isinstance(reply, dict) or "error" in reply:
            raise ProviderError("local_model_error", "Ollama returned an error")
        return from_body(reply, s.model, self._calls)
