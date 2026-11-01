"""Claude Messages API adapter (EMP-9). Every generation call goes through `guarded_call`.

Scope is deliberately narrow: first-party Claude API, `claude-haiku-5-5`, global routing,
standard tier, prompts below the 100k-token price tier, no prompt caching requested, no batch,
no server tools. Anything else reported back in `usage` has no configured price and puts the
ledger on hold.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from typing import Any

from .budget import CallRequest, CallResult, Ledger, guarded_call
from .pricing import PriceTable

MODEL = "claude-haiku-5-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"  # read by this process only; never logged or returned
# Haiku 5.5 tool-use system prompt with tool_choice auto (tool-use overview, 2026-11-01).
TOOL_SYSTEM_PROMPT_TOKENS = 286
# Margin for role/message framing and request configuration rendered into the prompt.
REQUEST_OVERHEAD_TOKENS = 1_000


class AdapterConfigError(Exception):
    """Live generation cannot be enabled with the current configuration."""


class PromptTooLarge(Exception):
    """The conservative prompt bound is above the configured limit; nothing was sent."""


@dataclass(frozen=True)
class GenerationSettings:
    model: str = MODEL
    effort: str = "medium"  # explicit baseline; Haiku 5.5 default is also medium
    thinking: str = "adaptive"
    thinking_display: str = "omitted"  # reasoning is never shown to the customer
    max_output_tokens: int = 4_096  # includes thinking tokens
    max_prompt_tokens: int = 90_000  # below the 100k price tier
    call_timeout_s: float = 60.0


@dataclass(frozen=True)
class CallContext:
    run_id: str
    scenario_id: str | None = None
    config_version: str = "unversioned"
    data_version: str = "unknown"
    budget: str = "development"
    run_cap_micro_usd: int | None = None
    timeout_s: float = 60.0  # remaining turn time; the call timeout is the smaller value


@dataclass(frozen=True)
class ModelReply:
    content: list[dict]  # blocks as plain dicts, passed back unchanged within a tool loop
    stop_reason: str | None
    op_id: str | None = None  # ledger operation, for run reports only


def prompt_token_bound(
    system: str, messages: list[dict], tools: list[dict], max_output_tokens: int
) -> int:
    """Upper bound of input tokens without calling the token-counting API.

    A token covers at least one UTF-8 byte, so the payload size in bytes bounds the text.
    Thinking blocks are passed back as encrypted signatures whose token cost is not visible,
    so each earlier assistant message in the loop adds a full output budget.
    """
    payload = json.dumps(
        {"system": system, "messages": messages, "tools": tools}, ensure_ascii=False
    ).encode("utf-8")
    thinking_messages = sum(
        1
        for m in messages
        if m["role"] == "assistant"
        and isinstance(m["content"], list)
        and any(b.get("type") in ("thinking", "redacted_thinking") for b in m["content"])
    )
    return (
        len(payload)
        + TOOL_SYSTEM_PROMPT_TOKENS
        + REQUEST_OVERHEAD_TOKENS
        + thinking_messages * max_output_tokens
    )


def map_usage(usage: Any) -> dict[str, int] | None:
    """Reported usage -> ledger categories, without double counting.

    `input_tokens` excludes cache reads/writes, and `output_tokens` already includes thinking,
    so the categories are disjoint. Only explicitly reported integers are copied; a missing
    field stays missing and the ledger treats the usage as incomplete. Billing conditions this
    configuration has no price for become unpriced categories (ledger hold).
    """
    if usage is None:
        return None
    mapped: dict[str, int] = {}

    def copy(field: str, category: str) -> None:
        value = getattr(usage, field, None)
        if type(value) is int:
            mapped[category] = value

    copy("input_tokens", "input")
    copy("output_tokens", "output")
    copy("cache_read_input_tokens", "cache_read")
    total = getattr(usage, "cache_creation_input_tokens", None)
    detail = getattr(usage, "cache_creation", None)
    five = getattr(detail, "ephemeral_5m_input_tokens", None)
    hour = getattr(detail, "ephemeral_1h_input_tokens", None)
    if type(five) is int and type(hour) is int:
        mapped["cache_write_5m"], mapped["cache_write_1h"] = five, hour
        if type(total) is int and total != five + hour:
            mapped["cache_write_unreconciled"] = total
    elif total == 0 and type(total) is int:
        mapped["cache_write_5m"] = mapped["cache_write_1h"] = 0  # explicit zero total
    elif type(total) is int:
        mapped["cache_write_unattributed"] = total  # duration unknown: no price applies

    tier = getattr(usage, "service_tier", None)
    if tier not in (None, "standard"):
        mapped[f"service_tier_{tier}"] = 1
    geo = getattr(usage, "inference_geo", None)
    if geo not in (None, "global"):
        mapped[f"inference_geo_{geo}"] = 1
    speed = getattr(usage, "speed", None)
    if speed not in (None, "standard"):
        mapped[f"speed_{speed}"] = 1
    server = getattr(usage, "server_tool_use", None)
    if server is not None:
        requests = sum(v for v in vars(server).values() if type(v) is int) if server else 0
        if requests:
            mapped["server_tool_use"] = requests
    return mapped


def _new_client(timeout_s: float) -> Any:
    import anthropic

    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise AdapterConfigError(f"{API_KEY_ENV} is not set for the backend process")
    # max_retries=0: each retry would be a new paid request; the ledger accounts one attempt.
    return anthropic.Anthropic(api_key=key, max_retries=0, timeout=timeout_s)


class AnthropicGenerator:
    mode = "live"

    def __init__(
        self,
        ledger: Ledger,
        pricing: PriceTable,
        settings: GenerationSettings = GenerationSettings(),
        client: Any = None,
    ):
        if ledger.mode != "live":
            raise AdapterConfigError("live generation requires a ledger created in live mode")
        if pricing.fictitious:
            raise AdapterConfigError("live generation cannot use fictitious prices")
        price = pricing.models.get(("anthropic", settings.model))
        if price is None:
            raise AdapterConfigError(f"no verified price for anthropic/{settings.model}")
        if (
            price.max_prompt_tokens is not None
            and settings.max_prompt_tokens > price.max_prompt_tokens
        ):
            raise AdapterConfigError("max_prompt_tokens is above the priced tier")
        self.ledger = ledger
        self.pricing = pricing
        self.settings = settings
        self.client = client if client is not None else _new_client(settings.call_timeout_s)

    def describe(self) -> dict:
        s = self.settings
        return {
            "provider": "anthropic",
            "model": s.model,
            "thinking": s.thinking,
            "thinking_display": s.thinking_display,
            "effort": s.effort,
            "max_output_tokens": s.max_output_tokens,
            "max_prompt_tokens": s.max_prompt_tokens,
            "call_timeout_s": s.call_timeout_s,
            "sdk_retries": 0,
        }

    def create(
        self, *, system: str, messages: list[dict], tools: list[dict], context: CallContext
    ) -> ModelReply:
        s = self.settings
        bound = prompt_token_bound(system, messages, tools, s.max_output_tokens)
        if bound > s.max_prompt_tokens:
            raise PromptTooLarge(f"prompt bound {bound} > {s.max_prompt_tokens}")
        timeout = max(1.0, min(s.call_timeout_s, context.timeout_s))
        request = CallRequest(
            op_id=f"{context.run_id}:{secrets.token_hex(6)}",
            budget=context.budget,
            provider="anthropic",
            model=s.model,
            # Prompt tokens fall in exactly one of the four prompt categories; reserving the
            # bound in each is deliberately conservative (unrequested caching stays covered).
            limits={
                "input": bound,
                "cache_write_5m": bound,
                "cache_write_1h": bound,
                "cache_read": bound,
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
            "max_tokens": s.max_output_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
            "tool_choice": {"type": "auto"},
            "thinking": {"type": s.thinking, "display": s.thinking_display},
            "output_config": {"effort": s.effort},
        }

        def call(req: CallRequest) -> CallResult:
            message = self.client.messages.create(**params, timeout=req.timeout_s)
            return CallResult(message, map_usage(getattr(message, "usage", None)))

        message = guarded_call(self.ledger, self.pricing, request, call).value
        content = [block.model_dump(mode="json", exclude_none=True) for block in message.content]
        return ModelReply(content, message.stop_reason, request.op_id)
