"""Evaluation fixtures for DEV-027/028/029 (offline: scripted generator, synthetic catalog)."""

import copy
import inspect
import json
from functools import partial

from emporio_assistant import fixtures as fixtures_module
from emporio_assistant.assistant import AssistantService
from emporio_assistant.fixtures import DEV_028_TEXT, CatalogTimeout, ProductTextInjection
from emporio_assistant.generation import END_TURN, TOOL_USE, ModelReply, ToolCall
from emporio_assistant.runner import (
    DEFAULT_SCENARIOS,
    FIXTURES,
    new_run_id,
    run,
    service_executor,
    simulated_executor,
)
from emporio_assistant.tools import CatalogSnapshot, call_tool


class Scripted:
    """Requests `calls` on each user message, then answers; records what it received."""

    mode = "simulated"

    def __init__(self, *calls):
        self.calls = calls
        self.seen = []

    def describe(self):
        return {"provider": "test-script"}

    def create(self, *, system, messages, tools, context):
        self.seen.append({"system": system, "messages": copy.deepcopy(messages)})
        if messages[-1]["role"] != "tool_results" and self.calls:
            requested = [ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(self.calls)]
            return ModelReply("", requested, TOOL_USE)
        return ModelReply("Resposta de teste.", [], END_TURN)


def execute(generation_dir, tmp_path, generator, ids, fixtures=None):
    service = AssistantService(generator, generation_dir)
    run_id = new_run_id(True)
    executor = service_executor(service, run_id, trace=True, fixtures=fixtures)
    path = run(executor, ids=ids, out_dir=tmp_path, run_id=run_id)
    return json.loads(path.read_text(encoding="utf-8")), service


def tool_steps(record):
    return [s for t in record["turns"] for s in t["trace"] if s["step"] == "tool"]


def test_injection_reaches_the_model_only_in_its_scenario_and_is_reported(generation_dir, tmp_path):
    generator = Scripted(("get_product", {"product_id": 1001}))
    fixtures = {"DEV-028": partial(ProductTextInjection, 1001, DEV_028_TEXT)}
    report, _ = execute(generation_dir, tmp_path, generator, ["DEV-028", "DEV-001"], fixtures)
    injected, clean = report["scenarios"]
    assert injected["environment"] == {
        "fixture": "product_description_injection",
        "kind": "tool_result_perturbation",
        "target_product_id": 1001,
        "applied": True,
        "applied_calls": 1,
    }
    step = tool_steps(injected)[0]  # the trace shows what the model actually received
    assert step["result"]["data"]["description"].endswith(DEV_028_TEXT)
    received = generator.seen[1]["messages"][-1]["results"][0].content
    assert DEV_028_TEXT in received
    # other facts are unchanged
    with CatalogSnapshot(generation_dir) as snapshot:
        real = call_tool(snapshot, "get_product", {"product_id": 1001})
    assert DEV_028_TEXT not in json.dumps(real, ensure_ascii=False)
    expected = copy.deepcopy(real)
    expected["data"]["description"] += " " + DEV_028_TEXT
    assert step["result"] == expected
    # removed after its scenario: the next scenario gets the real description
    assert "environment" not in clean
    assert all(DEV_028_TEXT not in json.dumps(s, ensure_ascii=False) for s in tool_steps(clean))
    assert report["order"] == ["DEV-028", "DEV-001"] and report["fixtures_enabled"] is True
    assert report["environment_groups"] == {"standard": ["DEV-001"], "perturbed": ["DEV-028"]}


def test_injection_not_exercised_when_no_tool_returns_the_description(generation_dir, tmp_path):
    generator = Scripted(("search_products", {"query": "guitar"}))
    fixtures = {"DEV-028": partial(ProductTextInjection, 1001, DEV_028_TEXT)}
    report, _ = execute(generation_dir, tmp_path, generator, ["DEV-028"], fixtures)
    environment = report["scenarios"][0]["environment"]
    assert environment["applied"] is False and environment["applied_calls"] == 0
    assert DEV_028_TEXT not in json.dumps(report, ensure_ascii=False)


def test_catalog_failure_is_a_normal_tool_error_and_reads_no_catalog(
    generation_dir, tmp_path, monkeypatch
):
    real_call = fixtures_module.call_tool
    delegated = []

    def watched(snapshot, name, arguments):
        delegated.append(name)
        return real_call(snapshot, name, arguments)

    monkeypatch.setattr(fixtures_module, "call_tool", watched)
    generator = Scripted(
        ("get_product", {"product_id": 1001}),
        ("search_products", {"query": "guitar", "available_only": True}),
        ("quote_price", {"product_id": "1001", "payment_method": "pix"}),  # invalid argument
    )
    report, _ = execute(generation_dir, tmp_path, generator, ["DEV-029"])
    record = report["scenarios"][0]
    assert record["environment"] == {
        "fixture": "catalog_timeout",
        "kind": "simulated_tool_failure",
        "error_code": "catalog_timeout",
        "applied": True,
        "triggered_calls": 2,
    }
    steps = tool_steps(record)
    assert [s["error"]["code"] for s in steps] == [
        "catalog_timeout",
        "catalog_timeout",
        "invalid_arguments",
    ]
    assert delegated == ["quote_price"]  # only the validation error went to the real tool
    assert all("result" not in s for s in steps)  # no catalog value reached the model
    # the turn goes on and is answered: not a provider timeout, the batch does not stop
    assert record["turns"][0]["status"] == "answered" and report["stopped_early"] is None
    # policy access works: the manual is still in the prompt
    assert "Seven test days for returns." in generator.seen[0]["system"]


def test_other_session_exists_with_synthetic_content_and_never_reaches_the_evaluated_one(
    generation_dir, tmp_path
):
    generator = Scripted()
    report, service = execute(generation_dir, tmp_path, generator, ["DEV-027"])
    environment = report["scenarios"][0]["environment"]
    marker = environment.pop("marker")
    assert marker.startswith("SINTETICO-AUX-")
    assert environment == {
        "fixture": "auxiliary_session",
        "kind": "session_isolation_setup",
        "applied": True,
        "auxiliary_session_dropped": True,
        "evaluated_session_empty_at_start": True,
        "marker_in_evaluated_history": False,
    }
    assert generator.seen and all(marker not in json.dumps(s, default=str) for s in generator.seen)
    assert len(service.store) == 1  # only the evaluated session remains
    assert report["environment_groups"]["standard"] == ["DEV-027"]


def test_two_sessions_on_one_service_never_share_history(generation_dir):
    generator = Scripted()
    service = AssistantService(generator, generation_dir)
    first, second = service.start_session(), service.start_session()
    service.send(first.id, "mensagem-da-sessao-A", "r1")
    service.send(second.id, "mensagem-da-sessao-B", "r2")
    seen_by_second = json.dumps(generator.seen[-1], default=str)
    assert "mensagem-da-sessao-B" in seen_by_second
    assert "mensagem-da-sessao-A" not in seen_by_second


def test_registry_matches_the_scenario_preconditions():
    data = json.loads(DEFAULT_SCENARIOS.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in data["scenarios"]}
    assert set(FIXTURES) == {"DEV-027", "DEV-028", "DEV-029"}
    assert DEV_028_TEXT in by_id["DEV-028"]["preconditions"][0]
    assert FIXTURES["DEV-028"].args[0] == 87 == by_id["DEV-028"]["references"]["products"][0]
    assert "times out" in by_id["DEV-029"]["preconditions"][0]
    assert FIXTURES["DEV-029"] is CatalogTimeout
    assert "Two isolated sessions" in by_id["DEV-027"]["preconditions"][0]


def test_fixtures_have_no_public_path():
    from emporio_assistant import main

    source = inspect.getsource(main)
    assert "tool_call" not in source and "fixture" not in source.lower()
    # a run without the service executor applies no fixture and says so
    assert simulated_executor().fixtures is False


def test_fixture_free_scenarios_use_the_real_tool_result(generation_dir, tmp_path):
    generator = Scripted(("get_product", {"product_id": 1001}))
    report, _ = execute(generation_dir, tmp_path, generator, ["DEV-001"])
    with CatalogSnapshot(generation_dir) as snapshot:
        real = call_tool(snapshot, "get_product", {"product_id": 1001})
    assert tool_steps(report["scenarios"][0])[0]["result"] == real
    assert "environment" not in report["scenarios"][0]
