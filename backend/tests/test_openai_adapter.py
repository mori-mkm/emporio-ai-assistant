"""OpenAI Responses adapter with a FAKE client (offline, real SDK response objects, no socket,
no key). Protocol, conversion and accounting only: not evidence of GPT-6 Luna behaviour."""

import copy
import json

import httpx2
import openai
import pytest

from emporio_assistant import openai_adapter
from emporio_assistant.agent import Agent
from emporio_assistant.assistant import AssistantService
from emporio_assistant.budget import UncertainUsage
from emporio_assistant.generation import AdapterConfigError, CallContext
from emporio_assistant.openai_adapter import OpenAIGenerator, OpenAISettings, map_usage
from emporio_assistant.tools import CatalogSnapshot

CTX = CallContext(run_id="test-run")
USAGE = {
    "input_tokens": 1000,
    "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
    "output_tokens": 300,
    "output_tokens_details": {"reasoning_tokens": 120},
    "total_tokens": 1300,
}


def response(output, status="completed", usage=USAGE, incomplete=None, **extra):
    from openai.types.responses import Response

    body = {
        "id": "resp_test",
        "created_at": 0,
        "model": "gpt-6-luna",
        "object": "response",
        "status": status,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "output": output,
        "service_tier": "default",
        **extra,
    }
    if usage is not None:
        body["usage"] = usage
    if incomplete:
        body["incomplete_details"] = {"reason": incomplete}
    return Response.model_validate(body)


def calls(*pairs, reasoning=True):
    items = [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC"}]
    items = items if reasoning else []
    for i, (name, args) in enumerate(pairs):
        arguments = args if isinstance(args, str) else json.dumps(args)
        items.append(
            {
                "type": "function_call",
                "id": f"fc_{i}",
                "call_id": f"call_{i}_{name}",
                "name": name,
                "arguments": arguments,
                "status": "completed",
            }
        )
    return response(items)


def answer(value, **kwargs):
    content = [{"type": "output_text", "text": value, "annotations": []}]
    item = {"type": "message", "id": "m1", "role": "assistant", "status": "completed"}
    return response([{**item, "content": content}], **kwargs)


class FakeOpenAI:
    """Scripted `client.responses.create`; items are Responses or exceptions."""

    def __init__(self, *script):
        self.script, self.requests = list(script), []
        self.responses = self

    def create(self, **params):
        self.requests.append(copy.deepcopy(params))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make(live_ledger, pricing, *script, settings=OpenAISettings()):
    client = FakeOpenAI(*script)
    return OpenAIGenerator(live_ledger, pricing, settings, client=client), client


@pytest.fixture
def snap(generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        yield snapshot


def states(ledger):
    return ledger.summary()["operations_by_state"]


def test_tool_round_trip_replays_reasoning_and_links_call_ids(live_ledger, pricing, snap):
    generator, client = make(
        live_ledger,
        pricing,
        calls(("get_product", {"product_id": 1001})),
        answer("Custa R$ 1.049,33."),
    )
    result = Agent(generator).run_turn(snap, [], "Quanto custa a Test Guitar A?", CTX)
    assert result.status == "answered" and result.text == "Custa R$ 1.049,33."
    first, second = client.requests
    assert first["model"] == "gpt-6-luna" and first["reasoning"] == {"effort": "low"}
    assert first["max_output_tokens"] == 4096 and first["store"] is False
    assert first["truncation"] == "disabled" and first["tool_choice"] == "auto"
    assert first["service_tier"] == "default"
    assert not {"temperature", "top_p", "previous_response_id"} & set(first)
    assert 0 < first["timeout"] <= 60
    assert 'data_generation="' + snap.id in first["instructions"]
    assert first["tools"][0]["type"] == "function" and first["tools"][0]["strict"] is False
    assert first["input"] == [{"role": "user", "content": "Quanto custa a Test Guitar A?"}]
    reasoning, call, output = second["input"][1:]
    assert reasoning["type"] == "reasoning" and reasoning["encrypted_content"] == "ENC"
    assert call["type"] == "function_call" and call["call_id"] == "call_0_get_product"
    assert output["type"] == "function_call_output" and output["call_id"] == call["call_id"]
    assert json.loads(output["output"])["data"]["offer_price"]["cents"] == 104933
    # no Anthropic-specific blocks are ever sent to OpenAI
    blob = json.dumps(client.requests)
    assert '"tool_result"' not in blob and '"tool_use"' not in blob and "signature" not in blob
    assert states(live_ledger) == {"settled": 2}


def test_two_calls_in_one_response_are_answered_in_order(live_ledger, pricing, snap):
    generator, client = make(
        live_ledger,
        pricing,
        calls(("get_product", {"product_id": 1001}), ("list_categories", {})),
        answer("Pronto."),
    )
    result = Agent(generator).run_turn(snap, [], "Detalhes e categorias?", CTX)
    assert result.tools_used == ["get_product", "list_categories"]
    outputs = [i for i in client.requests[1]["input"] if i.get("type") == "function_call_output"]
    assert [o["call_id"] for o in outputs] == ["call_0_get_product", "call_1_list_categories"]


def test_invalid_arguments_and_unknown_tool_go_back_as_errors(live_ledger, pricing, snap):
    generator, client = make(
        live_ledger,
        pricing,
        calls(("get_product", "{not json"), ("drop_table", {"name": "products"})),
        answer("Não consegui consultar."),
    )
    Agent(generator).run_turn(snap, [], "Oi", CTX)
    outputs = [i for i in client.requests[1]["input"] if i.get("type") == "function_call_output"]
    codes = [json.loads(o["output"])["error"]["code"] for o in outputs]
    assert codes == ["invalid_arguments", "unknown_tool"]


def test_conversation_continues_across_turns_with_text_history(
    generation_dir, live_ledger, pricing
):
    generator, client = make(
        live_ledger,
        pricing,
        calls(("list_categories", {})),
        answer("Temos violões."),
        answer("Sim, o mesmo."),
    )
    service = AssistantService(generator, generation_dir)
    session = service.start_session(run_id="test-run")
    service.send(session.id, "Que categorias vocês têm?", "r1")
    service.send(session.id, "E esse tem estoque?", "r2")
    third = client.requests[2]["input"]
    assert third == [
        {"role": "user", "content": "Que categorias vocês têm?"},
        {"role": "assistant", "content": "Temos violões."},
        {"role": "user", "content": "E esse tem estoque?"},
    ]


@pytest.mark.parametrize(
    "reply,status",
    [
        (
            lambda: response([], status="incomplete", incomplete="max_output_tokens"),
            "limit_reached",
        ),
        (lambda: response([], status="incomplete", incomplete="content_filter"), "refused"),
        (
            lambda: response(
                [
                    {
                        "type": "message",
                        "id": "m1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "não"}],
                    }
                ]
            ),
            "refused",
        ),
        (lambda: response([], status="failed"), "incomplete"),
        (lambda: answer("   "), "incomplete"),
    ],
)
def test_incomplete_refused_or_failed_replies_never_invent_an_answer(
    live_ledger, pricing, snap, reply, status
):
    generator, _ = make(live_ledger, pricing, reply())
    result = Agent(generator).run_turn(snap, [], "Oi", CTX)
    assert result.status == status and result.sources == []


def test_timeout_keeps_the_reservation_and_is_not_retried(live_ledger, pricing, snap):
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    generator, client = make(live_ledger, pricing, openai.APITimeoutError(request=request))
    with pytest.raises(UncertainUsage):
        Agent(generator).run_turn(snap, [], "Oi", CTX)
    assert len(client.requests) == 1 and states(live_ledger) == {"uncertain": 1}
    held = live_ledger.summary()["budgets"]["development"]
    assert held["confirmed_micro_usd"] == 0 and held["available_micro_usd"] < 7_000_000


def test_usage_maps_to_disjoint_categories_and_settles_exactly(live_ledger, pricing, snap):
    mapped = map_usage(response([], usage=USAGE).usage, "default")
    # 1000 input = 700 plain + 200 cached + 100 written; reasoning is inside the 300 output
    assert mapped == {"input": 700, "cache_read": 200, "cache_write_30m": 100, "output": 300}
    generator, _ = make(live_ledger, pricing, answer("Oi.", usage=USAGE))
    Agent(generator).run_turn(snap, [], "Oi", CTX)
    # 700x0.10 + 200x0.01 + 100x0.125 + 300x0.50 = 234.5 -> 235 µUSD
    assert live_ledger.summary()["budgets"]["development"]["confirmed_micro_usd"] == 235


def test_missing_usage_fields_are_not_zero_and_put_the_ledger_on_hold(live_ledger, pricing, snap):
    from openai.types.responses import ResponseUsage
    from openai.types.responses.response_usage import InputTokensDetails

    # The SDK builds responses without strict validation, so a field can be absent at runtime.
    partial = ResponseUsage.model_construct(
        input_tokens=1000,
        input_tokens_details=InputTokensDetails.model_construct(cached_tokens=200),
        output_tokens=300,
    )
    assert "input" not in map_usage(partial)
    reply = answer("Oi.")
    reply.usage = partial
    generator, _ = make(live_ledger, pricing, reply)
    with pytest.raises(UncertainUsage):
        Agent(generator).run_turn(snap, [], "Oi", CTX)
    summary = live_ledger.summary()
    assert summary["budgets"]["development"]["confirmed_micro_usd"] == 0
    assert summary["open_holds"]


def test_unpriced_conditions_are_flagged_but_zero_hosted_tool_counts_are_not():
    usage = response([], usage=USAGE).usage
    assert "service_tier_priority" in map_usage(usage, "priority")
    assert "service_tier_auto" in map_usage(usage, "auto")
    assert "service_tier_unreported" in map_usage(usage, None)
    zero = {"web_search": {"num_requests": 0}}
    assert "server_tool_use" not in map_usage(usage, "default", zero)
    assert "server_tool_use" in map_usage(usage, "default", {"web_search": {"num_requests": 2}})


def test_missing_key_refuses_before_any_client_or_call(live_ledger, pricing, monkeypatch):
    monkeypatch.delenv(openai_adapter.API_KEY_ENV, raising=False)
    with pytest.raises(AdapterConfigError, match="not set"):
        OpenAIGenerator(live_ledger, pricing)


def test_sdk_client_is_built_without_retries(monkeypatch):
    monkeypatch.setenv(openai_adapter.API_KEY_ENV, "test-placeholder-not-a-key")
    client = openai_adapter._new_client(60.0)
    assert client.max_retries == 0 and client.timeout == 60.0  # built only; nothing is sent


def test_configuration_without_price_or_above_the_tier_is_refused(live_ledger, pricing):
    with pytest.raises(AdapterConfigError, match="no verified price"):
        make(live_ledger, pricing, settings=OpenAISettings(model="gpt-6-unknown"))
    with pytest.raises(AdapterConfigError, match="priced tier"):
        make(live_ledger, pricing, settings=OpenAISettings(max_prompt_tokens=300_000))
