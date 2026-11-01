"""OpenAI Responses API adapter (EMP-11) for `gpt-6-luna`. Every call goes through `guarded_call`.

Checked 2026-11-01 against developers.openai.com (models/gpt-6-luna, guides/function-calling,
guides/reasoning, guides/prompt-caching) and SDK openai 3.42.1:
- function tools are top-level {type, name, description, parameters, strict}; a call comes back
  as a `function_call` output item (`call_id`, JSON-string `arguments`) and is answered with a
  `function_call_output` item carrying the same `call_id`;
- with store=false, reasoning items carry `encrypted_content` and must be replayed with the tool
  outputs; they stay in the private reply state and never reach traces or clients;
- `max_output_tokens` includes reasoning tokens; hitting it gives status `incomplete`;
- usage: `input_tokens` includes `cached_tokens` (reads) and `cache_write_tokens`;
  `output_tokens` includes `reasoning_tokens`.
Scope: standard tier, default 30-minute cache writes, prompts below the 272k long-context tier,
no built-in tools, no data residency. Anything else reported has no configured price (hold).
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from typing import Any

from .budget import CallRequest, CallResult, Ledger, guarded_call
from .generation import (
    END_TURN,
    MAX_TOKENS,
    OTHER,
    REFUSAL,
    TOOL_USE,
    AdapterConfigError,
    CallContext,
    ModelReply,
    PromptTooLarge,
    ToolCall,
    byte_bound,
)
from .pricing import PriceTable

MODEL = "gpt-6-luna"
API_KEY_ENV = "OPENAI_API_KEY"  # read by this process only; never logged or returned
REQUEST_OVERHEAD_TOKENS = 1_000  # role framing and request configuration rendered as input


@dataclass(frozen=True)
class OpenAISettings:
    model: str = MODEL
    # Starting hypothesis for cost/latency. Not the same compute as Haiku's "medium": effort
    # labels are provider-specific and do not mean equal reasoning budgets.
    reasoning_effort: str = "low"
    max_output_tokens: int = 4_096  # includes reasoning tokens
    max_prompt_tokens: int = 90_000  # same bound as the Haiku baseline; far below 272k
    call_timeout_s: float = 60.0


def to_input(messages: list[dict]) -> list[dict]:
    """Neutral conversation -> Responses input items. A tool-requesting reply is replayed from
    its own output items (reasoning + function_call); results become function_call_output."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "tool_results":
            out += [
                {"type": "function_call_output", "call_id": r.tool_call_id, "output": r.content}
                for r in m["results"]
            ]
        elif "tool_calls" in m:
            items = m["state"]
            if items is None:  # not produced by this adapter: rebuild the calls only
                items = [
                    {
                        "type": "function_call",
                        "call_id": c.id,
                        "name": c.name,
                        "arguments": json.dumps(c.arguments, ensure_ascii=False),
                    }
                    for c in m["tool_calls"]
                ]
            out += items
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def to_tools(tools: list[dict]) -> list[dict]:
    # strict=False: the schemas have optional fields with defaults, which strict mode rejects;
    # arguments are validated by tools.call_tool either way.
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
            "strict": False,
        }
        for t in tools
    ]


def prompt_token_bound(
    instructions: str, items: list[dict], tools: list[dict], max_output: int
) -> int:
    """Bytes of the request payload plus one output budget per replayed reasoning item (their
    encrypted content may stand for up to that many reasoning tokens)."""
    reasoning = sum(1 for i in items if i.get("type") == "reasoning")
    return byte_bound(instructions, items, tools) + REQUEST_OVERHEAD_TOKENS + reasoning * max_output


def _int(obj: Any, name: str) -> int | None:
    value = getattr(obj, name, None) if obj is not None else None
    return value if type(value) is int else None


def map_usage(usage: Any, service_tier: Any = None, tool_usage: Any = None) -> dict | None:
    """Reported usage -> disjoint ledger categories. Cached reads and cache writes are subsets of
    `input_tokens`, reasoning is a subset of `output_tokens`: nothing is counted twice. A field
    that is not reported stays missing (the ledger then holds); nothing defaults to zero."""
    if usage is None:
        return None
    mapped: dict[str, int] = {}
    total_in = _int(usage, "input_tokens")
    details = getattr(usage, "input_tokens_details", None)
    cached = _int(details, "cached_tokens")
    writes = _int(details, "cache_write_tokens")
    if cached is not None:
        mapped["cache_read"] = cached
    if writes is not None:
        long_ttl = _int(getattr(details, "cache_write_tokens_details", None), "ttl_12h_tokens")
        if long_ttl:  # 12-hour writes were never requested and have no price here
            mapped["cache_write_12h"] = long_ttl
        mapped["cache_write_30m"] = writes - (long_ttl or 0)
    if None not in (total_in, cached, writes):
        plain = total_in - cached - writes
        if plain >= 0:
            mapped["input"] = plain
        else:
            mapped["usage_unreconciled"] = 1  # inconsistent report: no price, ledger holds
    output = _int(usage, "output_tokens")
    if output is not None:
        mapped["output"] = output
    if service_tier != "default":  # requested explicitly; unconfirmed or other tier: hold
        mapped[f"service_tier_{service_tier or 'unreported'}"] = 1
    if _nonzero(tool_usage):  # present with zero counts when no hosted tool ran
        mapped["server_tool_use"] = 1
    return mapped


def _nonzero(value: Any) -> bool:
    """Any positive count inside a hosted-tool usage report (no hosted tools are configured)."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        return any(_nonzero(v) for v in value.values())
    if isinstance(value, list):
        return any(_nonzero(v) for v in value)
    return type(value) in (int, float) and value > 0


def _stop(response: Any, calls: list[ToolCall], refused: bool) -> tuple[str, str]:
    status = getattr(response, "status", None)
    reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
    raw = f"{status}:{reason}" if reason else str(status)
    if refused or reason == "content_filter":
        return REFUSAL, raw
    if status == "incomplete" and reason == "max_output_tokens":
        return MAX_TOKENS, raw
    if status == "completed":
        return (TOOL_USE if calls else END_TURN), raw
    return OTHER, raw


def from_response(response: Any, op_id: str | None) -> ModelReply:
    items = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
    texts, calls, refused = [], [], False
    for item in items:
        if item.get("type") == "function_call":
            raw = item.get("arguments")
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                arguments = raw  # left as text: argument validation rejects it
            calls.append(ToolCall(str(item.get("call_id")), str(item.get("name")), arguments))
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    refused = True
    stop, raw_stop = _stop(response, calls, refused)
    return ModelReply("".join(texts), calls, stop, items, op_id, raw_stop=raw_stop)


def _new_client(timeout_s: float) -> Any:
    import openai

    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise AdapterConfigError(f"{API_KEY_ENV} is not set for the backend process")
    # max_retries=0: each retry would be a new paid request; the ledger accounts one attempt.
    return openai.OpenAI(api_key=key, max_retries=0, timeout=timeout_s)


class OpenAIGenerator:
    mode = "live"

    def __init__(
        self,
        ledger: Ledger,
        pricing: PriceTable,
        settings: OpenAISettings = OpenAISettings(),
        client: Any = None,
    ):
        if ledger.mode != "live":
            raise AdapterConfigError("live generation requires a ledger created in live mode")
        if pricing.fictitious:
            raise AdapterConfigError("live generation cannot use fictitious prices")
        price = pricing.models.get(("openai", settings.model))
        if price is None:
            raise AdapterConfigError(f"no verified price for openai/{settings.model}")
        if (
            price.max_prompt_tokens is not None
            and settings.max_prompt_tokens > price.max_prompt_tokens
        ):
            raise AdapterConfigError("max_prompt_tokens is above the priced tier")
        if not 0 < settings.max_output_tokens <= 128_000:
            raise AdapterConfigError("max_output_tokens must be between 1 and 128000")
        self.ledger = ledger
        self.pricing = pricing
        self.settings = settings
        self.client = client if client is not None else _new_client(settings.call_timeout_s)

    def describe(self) -> dict:
        s = self.settings
        return {
            "provider": "openai",
            "api": "responses",
            "model": s.model,
            "reasoning_effort": s.reasoning_effort,
            "max_output_tokens": s.max_output_tokens,
            "max_prompt_tokens": s.max_prompt_tokens,
            "call_timeout_s": s.call_timeout_s,
            "store": False,
            "truncation": "disabled",
            "parallel_tool_calls": True,
            "service_tier": "default",
            "sdk_retries": 0,
        }

    def create(
        self, *, system: str, messages: list[dict], tools: list[dict], context: CallContext
    ) -> ModelReply:
        s = self.settings
        items = to_input(messages)
        functions = to_tools(tools)
        bound = prompt_token_bound(system, items, functions, s.max_output_tokens)
        if bound > s.max_prompt_tokens:
            raise PromptTooLarge(f"prompt bound {bound} > {s.max_prompt_tokens}")
        timeout = max(1.0, min(s.call_timeout_s, context.timeout_s))
        request = CallRequest(
            op_id=f"{context.run_id}:{secrets.token_hex(6)}",
            budget=context.budget,
            provider="openai",
            model=s.model,
            # Each prompt token is billed in exactly one of the three prompt categories; the
            # bound is reserved in each (caching is automatic and cannot be ruled out).
            limits={
                "input": bound,
                "cache_read": bound,
                "cache_write_30m": bound,
                "output": s.max_output_tokens,
            },
            run_id=context.run_id,
            scenario_id=context.scenario_id,
            config_version=context.config_version,
            data_version=context.data_version,
            timeout_s=timeout,
            run_cap_micro_usd=context.run_cap_micro_usd,
        )
        params = {
            "model": s.model,
            "instructions": system,
            "input": items,
            "tools": functions,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "reasoning": {"effort": s.reasoning_effort},
            "max_output_tokens": s.max_output_tokens,
            "store": False,
            "truncation": "disabled",  # an oversized prompt fails instead of being cut
            "service_tier": "default",  # the only priced tier; the returned tier is checked
        }

        def call(req: CallRequest) -> CallResult:
            response = self.client.responses.create(**params, timeout=req.timeout_s)
            usage = map_usage(
                getattr(response, "usage", None),
                getattr(response, "service_tier", None),
                getattr(response, "tool_usage", None),
            )
            return CallResult(response, usage)

        response = guarded_call(self.ledger, self.pricing, request, call).value
        return from_response(response, request.op_id)
