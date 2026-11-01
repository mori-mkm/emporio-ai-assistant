"""Provider-neutral generation contract (EMP-11) shared by the agent and the adapters.

The agent speaks only these shapes; each adapter converts them to and from its provider's wire
format. Conversation messages are plain dicts:

- {"role": "user" | "assistant", "content": str}   text turns (session history)
- {"role": "assistant", "text": str, "tool_calls": [ToolCall], "state": <opaque>}
      a model reply that requested tools; `state` holds the provider items the provider
      needs to continue (e.g. thinking blocks or reasoning items). Only the adapter that
      produced it reads it; it is never logged, traced or returned to a client.
- {"role": "tool_results", "results": [ToolResult]}   answers to the previous tool calls
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Neutral stop reasons (the values the run trace records).
TOOL_USE, END_TURN, REFUSAL, MAX_TOKENS, OTHER = (
    "tool_use",
    "end_turn",
    "refusal",
    "max_tokens",
    "other",
)


class AdapterConfigError(Exception):
    """Generation cannot be enabled with the current configuration."""


class PromptTooLarge(Exception):
    """The conservative prompt bound is above the configured limit; nothing was sent."""


class ProviderError(Exception):
    """A non-ledger provider failure (local model): the turn and the batch stop."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


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
class ToolCall:
    id: str  # links the call to its result; synthesized when the provider has none
    name: str
    arguments: object  # as the provider returned them; validated only by tools.call_tool


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    name: str
    content: str  # JSON text given to the model
    is_error: bool


@dataclass(frozen=True)
class ModelReply:
    text: str
    tool_calls: list[ToolCall]
    stop: str  # one of the neutral stop reasons
    state: object = None  # provider items needed to continue (private, see module doc)
    op_id: str | None = None  # paid ledger operation, for run reports only
    raw_stop: str | None = None  # provider's own stop value, for run reports only
    metrics: dict = field(default_factory=dict)  # local-model counters and durations


def byte_bound(*parts: object) -> int:
    """UTF-8 size of the JSON payload: an upper bound of its tokens for byte-level tokenizers
    (every text token covers at least one byte)."""
    return len(json.dumps(parts, ensure_ascii=False).encode("utf-8"))
