"""Ollama adapter with a FAKE HTTP transport (offline: no Ollama server, no model, no socket).
Protocol, limits and run labelling only: whether qwen3:8b fits or behaves well is unmeasured."""

import copy
import json

import pytest

from emporio_assistant import ollama_adapter
from emporio_assistant.agent import Agent
from emporio_assistant.assistant import AssistantService, PublicError
from emporio_assistant.generation import AdapterConfigError, CallContext, ProviderError
from emporio_assistant.ollama_adapter import OllamaGenerator, OllamaSettings
from emporio_assistant.tools import CatalogSnapshot

CTX = CallContext(run_id="test-run")
METRICS = {
    "total_duration": 9_000_000_000,
    "load_duration": 4_000_000_000,
    "prompt_eval_count": 7000,
    "prompt_eval_duration": 3_000_000_000,
    "eval_count": 120,
    "eval_duration": 2_000_000_000,
}
DIGEST = "500a1f067a9f" + "0" * 52


def chat(content="", tool_calls=None, done_reason="stop", model="qwen3:8b", **extra):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = [
            {"function": {"index": i, "name": name, "arguments": args}}
            for i, (name, args) in enumerate(tool_calls)
        ]
    return {"model": model, "message": message, "done": True, "done_reason": done_reason,
            **METRICS, **extra}  # fmt: skip


class FakeOllama:
    """Scripted transport: /api/chat answers come from the script; other paths from `pages`."""

    def __init__(self, *script, pages=None):
        self.script, self.requests = list(script), []
        self.pages = pages or {}

    def __call__(self, method, path, body, timeout):
        self.requests.append((method, path, copy.deepcopy(body), timeout))
        if path != "/api/chat":
            return self.pages[path]
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make(*script, pages=None, **settings):
    transport = FakeOllama(*script, pages=pages)
    return OllamaGenerator(OllamaSettings(**settings), transport=transport), transport


def chats(transport):
    return [body for _, path, body, _ in transport.requests if path == "/api/chat"]


@pytest.fixture
def snap(generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        yield snapshot


def test_tool_round_trip_in_native_format_with_explicit_limits(snap):
    generator, transport = make(
        chat(tool_calls=[("get_product", {"product_id": 1001})]), chat("Custa R$ 1.049,33.")
    )
    result = Agent(generator).run_turn(snap, [], "Quanto custa a Test Guitar A?", CTX)
    assert result.status == "answered" and result.text == "Custa R$ 1.049,33."
    first, second = chats(transport)
    assert first["model"] == "qwen3:8b" and first["think"] is False and first["stream"] is False
    assert first["options"] == {"num_ctx": 32768, "num_predict": 2048}
    assert first["messages"][0]["role"] == "system"
    assert 'data_generation="' + snap.id in first["messages"][0]["content"]
    assert first["tools"][0]["type"] == "function" and "parameters" in first["tools"][0]["function"]
    assistant, tool = second["messages"][-2:]
    assert assistant["tool_calls"][0]["function"]["name"] == "get_product"
    assert tool["role"] == "tool" and tool["tool_name"] == "get_product"
    assert json.loads(tool["content"])["data"]["offer_price"]["cents"] == 104933
    blob = json.dumps(chats(transport))
    assert '"tool_result"' not in blob and '"tool_use"' not in blob and "signature" not in blob
    # local counters and durations kept per call, cold load apart from evaluation
    assert result.call_metrics[0]["load_ms"] == 4000 and result.call_metrics[0]["total_ms"] == 9000
    assert result.call_metrics[0]["prompt_eval_count"] == 7000 and result.op_ids == []


def test_two_calls_keep_their_order_and_distinct_ids(snap):
    generator, transport = make(
        chat(tool_calls=[("get_product", {"product_id": 1001}), ("list_categories", {})]),
        chat("Pronto."),
    )
    trace = []
    result = Agent(generator).run_turn(snap, [], "Detalhes e categorias?", CTX, trace)
    assert result.tools_used == ["get_product", "list_categories"]
    tools = [m for m in chats(transport)[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_name"] for m in tools] == ["get_product", "list_categories"]
    ids = [s["tool_use_id"] for s in trace if s["step"] == "tool"]
    assert len(set(ids)) == 2 and all(i.startswith("ollama_") for i in ids)
    assert trace[0]["metrics"]["eval_count"] == 120


def test_string_arguments_are_parsed_and_bad_ones_rejected(snap):
    generator, transport = make(
        chat(tool_calls=[("get_product", '{"product_id": 1001}'), ("get_product", "{oops")]),
        chat("Ok."),
    )
    Agent(generator).run_turn(snap, [], "Oi", CTX)
    tools = [m for m in chats(transport)[1]["messages"] if m["role"] == "tool"]
    outcomes = [json.loads(m["content"]) for m in tools]
    assert outcomes[0]["ok"] is True and outcomes[1]["error"]["code"] == "invalid_arguments"


def test_output_limit_and_unknown_end_never_invent_an_answer(snap):
    generator, _ = make(chat("parcial", done_reason="length"))
    assert Agent(generator).run_turn(snap, [], "Oi", CTX).status == "limit_reached"
    generator, _ = make(chat("x", done_reason="unload"))
    assert Agent(generator).run_turn(snap, [], "Oi", CTX).status == "incomplete"


def test_prompt_above_the_context_is_refused_before_sending(snap):
    from emporio_assistant.generation import PromptTooLarge

    generator, transport = make(chat("never"), num_ctx=4096, num_predict=2048)
    with pytest.raises(PromptTooLarge):
        Agent(generator).run_turn(snap, [], "Oi", CTX)
    assert chats(transport) == []  # nothing cut, nothing sent


def test_a_different_model_in_the_answer_is_an_error(snap):
    generator, _ = make(chat("Oi", model="qwen3:4b"))
    with pytest.raises(ProviderError, match="model_mismatch"):
        Agent(generator).run_turn(snap, [], "Oi", CTX)


def test_timeout_and_errors_stop_the_turn_with_public_codes(generation_dir):
    for failure, code in [
        (TimeoutError(), "provider_timeout"),
        (ConnectionRefusedError(), "provider_error"),
    ]:
        generator, _ = make(failure)
        service = AssistantService(generator, generation_dir)
        session = service.start_session(run_id="test-run")
        with pytest.raises(PublicError) as raised:
            service.send(session.id, "Oi", "r1")
        assert raised.value.code == code and raised.value.stops_batch is True


def test_only_one_local_inference_at_a_time(snap):
    from emporio_assistant.agent import AgentConfig

    generator, transport = make(chat("Oi"))
    agent = Agent(generator, AgentConfig(turn_timeout_s=1.0))  # waits at most the turn time
    assert ollama_adapter._ONE_AT_A_TIME.acquire(timeout=1)
    try:
        with pytest.raises(ProviderError, match="local_model_busy"):
            agent.run_turn(snap, [], "Oi", CTX)
    finally:
        ollama_adapter._ONE_AT_A_TIME.release()
    assert chats(transport) == []


@pytest.mark.parametrize(
    "settings,message",
    [
        ({"base_url": "http://192.168.0.10:11434"}, "loopback"),
        ({"base_url": "https://127.0.0.1:11434"}, "loopback"),
        ({"num_ctx": 65_536}, "num_ctx"),
        ({"num_ctx": 1024}, "num_ctx"),
        ({"num_predict": 40_000, "num_ctx": 32_768}, "num_predict"),
        ({"call_timeout_s": 0}, "call_timeout_s"),
    ],
)
def test_invalid_configuration_is_refused(settings, message):
    with pytest.raises(AdapterConfigError, match=message):
        OllamaSettings(**settings).validate()


def pages(digest=DIGEST, installed=True):
    tags = [{"name": "qwen3:8b", "digest": digest, "size": 5_200_000_000}] if installed else []
    loaded = [
        {
            "name": "qwen3:8b",
            "digest": digest,
            "size": 6_000_000_000,
            "size_vram": 4_500_000_000,
            "context_length": 32768,
        }
    ]
    return {
        "/api/version": {"version": "0.40.13"},
        "/api/tags": {"models": tags},
        "/api/ps": {"models": loaded},
    }


def test_model_identity_is_checked_and_never_substituted():
    generator, _ = make(pages=pages())
    info = generator.check_model()
    assert info["ollama_version"] == "0.40.13" and info["loaded"][0]["context_length"] == 32768
    assert info["loaded"][0]["gpu_share"] == 0.75
    with pytest.raises(AdapterConfigError, match="not installed"):
        make(pages=pages(installed=False))[0].check_model()
    with pytest.raises(AdapterConfigError, match="expected build"):
        make(pages=pages(digest="abcdef123456"))[0].check_model()


def test_local_run_is_labelled_real_local_inference_without_ledger(generation_dir, tmp_path):
    from emporio_assistant.runner import run, service_executor

    generator, _ = make(
        chat(tool_calls=[("list_categories", {})]), chat("Temos instrumentos."), pages=pages()
    )
    service = AssistantService(generator, generation_dir)
    executor = service_executor(service, "20261101T000000Z-abc123", trace=True)
    report = json.loads(
        run(
            executor, ids=["DEV-026"], out_dir=tmp_path, run_id="20261101T000000Z-abc123"
        ).read_text(encoding="utf-8")
    )
    assert report["simulated"] is False and report["execution"] == "local_inference"
    assert "SIMULATED" not in report["notice"] and "no API cost" in report["notice"]
    assert report["cost"] == {"api_usd": 0, "operational": "not measured"}
    assert "ledger" not in report  # no fictitious spend operation
    assert report["runtime"]["before"]["ollama_version"] == "0.40.13"
    assert report["runtime"]["after"]["loaded"][0]["size_vram"] == 4_500_000_000
    assert report["configuration"]["generator"]["num_ctx"] == 32768
    turn = report["scenarios"][0]["turns"][0]
    assert turn["status"] == "answered" and turn["op_ids"] == []
    assert [m["call"] for m in turn["call_metrics"]] == [1, 2]
    assert turn["trace_complete"] is True
