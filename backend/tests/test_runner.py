"""Offline runner (EMP-7): simulated, records only what ran, never grades."""

import json
from dataclasses import asdict
from decimal import Decimal

from emporio_assistant.budget import InsufficientBudget
from emporio_assistant.runner import DEFAULT_SCENARIOS, Executor, main, run, simulated_executor


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_smoke_run_is_labelled_simulated_and_ungraded(tmp_path):
    report = load(run(simulated_executor(), smoke=True, out_dir=tmp_path))
    assert report["simulated"] is True and report["graded"] is False
    assert "SIMULATED" in report["notice"] and report["run_id"].endswith("-simulated")
    assert [s["id"] for s in report["scenarios"]] == load(DEFAULT_SCENARIOS)["smoke_suite"]
    for record in report["scenarios"]:
        assert record["turns_executed"] == record["turns_total"]
        assert {t["text"] for t in record["turns"]} == {"[SIMULATED] No model was called."}
    # Expected answers are never copied into the run.
    assert "expect" not in json.dumps(report) and "must_not_claim" not in json.dumps(report)


def test_history_is_isolated_per_scenario(tmp_path):
    seen = []

    def respond(scenario_id, history, user):
        seen.append((scenario_id, len(history)))
        return "x"

    executor = Executor("probe", True, None, None, "test", respond)
    run(executor, ids=["DEV-020", "DEV-001"], out_dir=tmp_path)
    assert seen == [("DEV-020", 0), ("DEV-020", 2), ("DEV-020", 4), ("DEV-001", 0), ("DEV-001", 2)]


def test_errors_are_recorded_and_budget_errors_stop_the_run(tmp_path):
    def respond(scenario_id, history, user):
        if scenario_id == "DEV-002":
            raise RuntimeError("boom")
        if scenario_id == "DEV-003":
            raise InsufficientBudget("cap")
        return "x"

    executor = Executor("probe", True, None, None, "test", respond)
    report = load(run(executor, ids=["DEV-002", "DEV-003", "DEV-004"], out_dir=tmp_path))
    assert [(s["id"], s["error"]) for s in report["scenarios"]] == [
        ("DEV-002", "RuntimeError"),
        ("DEV-003", "InsufficientBudget"),
    ]
    assert report["stopped_early"] == "batch stopped: InsufficientBudget"
    assert report["not_run"] == ["DEV-004"]


def test_cli_rejects_unknown_ids(tmp_path, capsys):
    assert main(["--ids", "DEV-999", "--out-dir", str(tmp_path)]) == 1
    assert "unknown scenario ids" in capsys.readouterr().out


def test_service_executor_runs_scenarios_through_the_api_service(generation_dir, tmp_path):
    from emporio_assistant.assistant import AssistantService, SimulatedGenerator
    from emporio_assistant.runner import new_run_id, service_executor

    service = AssistantService(SimulatedGenerator(), generation_dir)
    run_id = new_run_id(True)
    report = load(
        run(
            service_executor(service, run_id),
            ids=["DEV-020", "DEV-001"],
            out_dir=tmp_path,
            run_id=run_id,
        )
    )
    assert report["simulated"] is True and report["executor"] == "assistant-service-simulated"
    turns = [t for s in report["scenarios"] for t in s["turns"]]
    assert all(t["text"].startswith("[Resposta simulada") for t in turns)
    assert {t["status"] for t in turns} == {"answered"}
    assert all(t["model_calls"] == 2 and t["tools_used"] == ["list_categories"] for t in turns)
    sessions = list(service.store._sessions.values())
    assert sorted(len(s.history) for s in sessions) == [4, 6]  # one session per scenario
    assert {s.run_id for s in sessions} == {run_id}


def test_live_pilot_requires_explicit_confirmation_and_a_small_cap(tmp_path, capsys):
    base = ["--smoke", "--executor", "live", "--out-dir", str(tmp_path)]
    assert main(base) == 1
    assert "--confirm-paid-run" in capsys.readouterr().out
    assert main([*base, "--confirm-paid-run", "--batch-cap-usd", "0.30"]) == 1
    assert "<= 0.25" in capsys.readouterr().out
    assert (
        main([*base, "--confirm-paid-run", "--batch-cap-usd", "0.25", "--max-model-calls", "9"])
        == 1
    )
    assert not list(tmp_path.iterdir())  # nothing ran


def live_service(generation_dir, live_ledger, pricing, *script):
    from conftest import FakeAnthropic

    from emporio_assistant.anthropic_adapter import AnthropicGenerator
    from emporio_assistant.assistant import AssistantService

    generator = AnthropicGenerator(live_ledger, pricing, client=FakeAnthropic(*script))
    return AssistantService(generator, generation_dir)


def test_turn_status_is_recorded_not_flattened_to_text(
    generation_dir, live_ledger, pricing, tmp_path
):
    from conftest import message, text

    from emporio_assistant.runner import new_run_id, service_executor

    service = live_service(
        generation_dir,
        live_ledger,
        pricing,
        text("Olá! Posso ajudar."),
        message([{"type": "text", "text": "parcial"}], "max_tokens"),
        message([], "end_turn"),
    )
    run_id = new_run_id(False)
    report = load(
        run(service_executor(service, run_id), ids=["DEV-020"], out_dir=tmp_path, run_id=run_id)
    )
    record = report["scenarios"][0]
    assert [t["status"] for t in record["turns"]] == ["answered", "limit_reached", "incomplete"]
    assert record["turns_executed"] == 3 and record["turns_answered"] == 1
    assert report["simulated"] is False and report["configuration"]["agent"]["max_model_calls"]
    # every turn links to its ledger operations; the run view shows usage and settled cost
    op_ids = [op for t in record["turns"] for op in t["op_ids"]]
    ledger = report["ledger"]
    assert [o["op_id"] for o in ledger["operations"]] == op_ids
    assert ledger["states"] == {"settled": 3} and ledger["open_holds"] == []
    assert ledger["confirmed_micro_usd"] == 3 * 200 == ledger["committed_micro_usd"]
    assert all(o["usage"]["input"] == 1000 for o in ledger["operations"])
    assert "signature" not in json.dumps(report)  # no reasoning blocks in the report


def test_provider_error_stops_the_batch_and_keeps_the_reservation(
    generation_dir, live_ledger, pricing, tmp_path
):
    import anthropic
    import httpx2

    from emporio_assistant.runner import new_run_id, service_executor

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    busy = anthropic.RateLimitError(
        "slow", response=httpx2.Response(429, request=request), body=None
    )
    service = live_service(generation_dir, live_ledger, pricing, busy)
    run_id = new_run_id(False)
    report = load(
        run(
            service_executor(service, run_id),
            ids=["DEV-004", "DEV-001"],
            out_dir=tmp_path,
            run_id=run_id,
        )
    )
    assert report["stopped_early"] == "batch stopped: provider_busy"
    assert report["scenarios"][0]["turns"][0]["status"] == "error"
    assert report["scenarios"][0]["error"] == "provider_busy"
    assert report["not_run"] == ["DEV-001"]
    ledger = report["ledger"]
    assert ledger["states"] == {"uncertain": 1} and ledger["confirmed_micro_usd"] == 0
    assert ledger["committed_micro_usd"] == ledger["operations"][0]["reserved_micro_usd"] > 0


def test_no_turn_starts_after_the_run_deadline(tmp_path):
    calls = []

    def respond(scenario_id, history, user):
        calls.append(scenario_id)
        return "x"

    executor = Executor("probe", True, None, None, "test", respond)
    report = load(run(executor, ids=["DEV-001", "DEV-004"], out_dir=tmp_path, max_run_s=0))
    assert calls == [] and report["stopped_early"] == "run time limit"
    assert report["scenarios"] == [] and report["not_run"] == ["DEV-001", "DEV-004"]


def traced_run(generation_dir, live_ledger, pricing, tmp_path, *script, trace=True, ids=None):
    from emporio_assistant.runner import new_run_id, service_executor

    service = live_service(generation_dir, live_ledger, pricing, *script)
    run_id = new_run_id(False)
    executor = service_executor(service, run_id, trace=trace)
    return load(run(executor, ids=ids or ["DEV-026"], out_dir=tmp_path, run_id=run_id))


def test_trace_records_what_ran_in_order_with_sources_and_dropped_citations(
    generation_dir, live_ledger, pricing, tmp_path
):
    from conftest import text, tool_use

    from emporio_assistant.tools import CatalogSnapshot, call_tool

    report = traced_run(
        generation_dir,
        live_ledger,
        pricing,
        tmp_path,
        tool_use(("get_product", {"product_id": 1001}), ("quote_price", {"product_id": "x9z"})),
        text("Custa R$ 1.049,33.\n[fontes: 2, 99.9]"),
    )
    assert report["report_format"] == 2 and report["trace_enabled"] is True
    turn = report["scenarios"][0]["turns"][0]
    assert [s["section"] for s in turn["sources"]] == ["2"]
    assert turn["dropped_citations"] == 1 and turn["dropped_sections"] == ["99.9"]
    steps = turn["trace"]
    assert [(s["step"], s.get("name")) for s in steps] == [
        ("model", None),
        ("tool", "get_product"),
        ("tool", "quote_price"),
        ("model", None),
    ]
    assert [s["op_id"] for s in steps if s["step"] == "model"] == turn["op_ids"]
    assert [o["op_id"] for o in report["ledger"]["operations"]] == turn["op_ids"]
    assert [s["stop"] for s in steps if s["step"] == "model"] == ["tool_use", "end_turn"]
    good, bad = steps[1], steps[2]
    with CatalogSnapshot(generation_dir) as snapshot:  # the same tool, same arguments
        assert good["result"] == call_tool(snapshot, "get_product", {"product_id": 1001})
    assert good["arguments"] == {"product_id": 1001} and good["tool_use_id"].startswith("toolu_")
    assert good["ok"] is True and good["truncated"] is False and good["call"] == 1
    # the rejected call keeps its place, with sanitized error metadata and no copied input
    assert bad["ok"] is False and bad["arguments"] is None
    assert bad["error"]["code"] == "invalid_arguments" and "x9z" not in json.dumps(bad)
    assert turn["trace_complete"] is True
    blob = json.dumps(report, ensure_ascii=False)
    assert "signature" not in blob and "Trust boundary" not in blob and "store_manual" not in blob


def test_trace_is_off_unless_the_runner_enables_it(generation_dir, live_ledger, pricing, tmp_path):
    from conftest import text

    report = traced_run(
        generation_dir, live_ledger, pricing, tmp_path, text("Olá. [fontes: 2]"), trace=False
    )
    turn = report["scenarios"][0]["turns"][0]
    assert report["trace_enabled"] is False
    assert "trace" not in turn and "trace_complete" not in turn
    assert [s["section"] for s in turn["sources"]] == ["2"]  # part of the public reply anyway


def test_oversized_trace_values_are_cut_and_flagged(
    generation_dir, live_ledger, pricing, tmp_path, monkeypatch
):
    from conftest import text, tool_use

    from emporio_assistant import agent

    monkeypatch.setattr(agent, "TRACE_LIMIT_CHARS", 50)
    report = traced_run(
        generation_dir,
        live_ledger,
        pricing,
        tmp_path,
        tool_use(("get_product", {"product_id": 1001})),
        text("Pronto."),
    )
    turn = report["scenarios"][0]["turns"][0]
    step = turn["trace"][1]
    assert step["truncated"] is True and isinstance(step["result"], str)
    assert len(step["result"]) == 50 and turn["trace_complete"] is False


def test_a_failed_turn_keeps_the_steps_before_the_failure(
    generation_dir, live_ledger, pricing, tmp_path
):
    import anthropic
    import httpx2
    from conftest import tool_use

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    busy = anthropic.RateLimitError(
        "slow", response=httpx2.Response(429, request=request), body=None
    )
    report = traced_run(
        generation_dir,
        live_ledger,
        pricing,
        tmp_path,
        tool_use(("get_product", {"product_id": 1001})),
        busy,
        ids=["DEV-026", "DEV-001"],
    )
    turn = report["scenarios"][0]["turns"][0]
    assert turn["status"] == "error" and turn["error"] == "provider_busy"
    assert [s["step"] for s in turn["trace"]] == ["model", "tool"]
    assert turn["trace_complete"] is False and report["not_run"] == ["DEV-001"]


def test_old_reports_mark_missing_evidence_instead_of_inventing_it():
    from emporio_assistant.runner import NOT_RECORDED, turn_evidence

    old = {"text": "Oi", "status": "answered", "tools_used": ["search_products"], "op_ids": ["a"]}
    evidence = turn_evidence(old)
    assert evidence["text"] == "Oi" and evidence["tools_used"] == ["search_products"]
    assert evidence["sources"] == evidence["trace"] == evidence["dropped_sections"] == NOT_RECORDED
    assert turn_evidence({"text": "Oi"})["status"] == NOT_RECORDED


def test_paid_runs_are_labelled_paid_and_simulated_ones_simulated(
    generation_dir, live_ledger, pricing, tmp_path
):
    from conftest import text

    paid = traced_run(generation_dir, live_ledger, pricing, tmp_path, text("Olá."), trace=False)
    assert paid["execution"] == "paid_api" and paid["simulated"] is False and "ledger" in paid
    assert "cost" not in paid and "runtime" not in paid
    simulated = load(run(simulated_executor(), ids=["DEV-026"], out_dir=tmp_path))
    assert simulated["execution"] == "simulated" and simulated["simulated"] is True


def test_cli_refuses_other_providers_before_any_call(tmp_path, capsys, monkeypatch):
    from emporio_assistant import openai_adapter

    monkeypatch.delenv(openai_adapter.API_KEY_ENV, raising=False)
    monkeypatch.setenv("EMPORIO_LEDGER_PATH", str(tmp_path / "absent.sqlite"))
    base = ["--ids", "DEV-026", "--out-dir", str(tmp_path)]
    paid = ["--executor", "live", "--provider", "openai", "--confirm-paid-run"]
    assert main([*base, *paid, "--batch-cap-usd", "0.05"]) == 1
    assert "live mode not ready" in capsys.readouterr().out
    assert main([*base, *paid]) == 1  # no cap
    monkeypatch.setenv("EMPORIO_OLLAMA_URL", "http://10.0.0.5:11434")
    assert main([*base, "--executor", "local"]) == 1
    assert "loopback" in capsys.readouterr().out
    assert main([*base, "--executor", "simulated", "--num-ctx", "8192"]) == 1
    assert list(tmp_path.glob("*.json")) == []  # nothing ran


def test_provider_must_match_the_mode_and_comes_only_from_backend_settings(generation_dir):
    import pytest

    from emporio_assistant.assistant import Settings, build_service
    from emporio_assistant.generation import AdapterConfigError

    def settings(mode, provider):
        return Settings(mode, generation_dir, generation_dir, generation_dir, (), False, provider)

    for mode, provider in [("simulated", "openai"), ("local", "anthropic"), ("live", "ollama")]:
        with pytest.raises(AdapterConfigError, match="not valid"):
            build_service(settings(mode, provider))
    assert build_service(settings("simulated", None)).mode == "simulated"


def test_api_and_runner_build_the_same_configuration(generation_dir, tmp_path, monkeypatch):
    """Same backend settings -> same agent configuration (hash, limits, policy) in the chat API
    and in the benchmark runner. With only mode, provider and cache set, the API reproduces
    the measured candidate d9f674fcc08d (4 model calls, full manual)."""
    from types import SimpleNamespace

    from conftest import FakeAnthropic

    from emporio_assistant import anthropic_adapter, runner
    from emporio_assistant.agent import AgentConfig
    from emporio_assistant.assistant import Settings, build_service
    from emporio_assistant.budget import Ledger

    Ledger.create(tmp_path / "ledger.sqlite", "live")
    monkeypatch.setattr(anthropic_adapter, "_new_client", lambda _timeout: FakeAnthropic())
    for name, value in (("EMPORIO_DATA_DIR", generation_dir), ("EMPORIO_ASSISTANT_MODE", "live"),
                        ("EMPORIO_LEDGER_PATH", tmp_path / "ledger.sqlite"),
                        ("EMPORIO_PROVIDER", "anthropic")):  # fmt: skip
        monkeypatch.setenv(name, str(value))
    versions = {}
    for policy, cache in (("full", "system-5m"), ("full", "off"), ("bm25", "off")):
        monkeypatch.setenv("EMPORIO_PROMPT_CACHE", cache)
        monkeypatch.setenv("EMPORIO_POLICY_CONTEXT", policy)
        api = build_service(Settings.from_env())  # what main.py builds
        args = SimpleNamespace(
            executor="live", provider="anthropic", confirm_paid_run=True,
            batch_cap_usd=Decimal("0.10"), num_ctx=None, num_predict=None, suite="development",
            trace=False, prompt_cache=cache, policy_context=policy,
            max_model_calls=AgentConfig.max_model_calls,  # the runner's CLI default
        )  # fmt: skip
        executor, _ = runner._service_executor(args)
        assert executor.config_version == api.agent.version
        assert executor.configuration["agent"] == asdict(api.agent.config)
        assert executor.configuration["policy_context"] == api.agent.policy.describe()
        versions[(policy, cache)] = api.agent.version
    assert versions[("full", "system-5m")] == "d9f674fcc08d"
    assert versions[("full", "off")] == "30baf5c5f663"
    assert api.agent.config.max_model_calls == 4


def test_frozen_haiku_candidate_configuration_hash():
    """Prompt revision 2 with server lookup records (2026-11-02). Earlier runs belong to the
    earlier prompt: 0b5879238a78 (no cache) and 14a0f85d715d (system-5m). Any change to the
    prompt, memory format, tools or Haiku settings must show up as a new hash here."""
    from emporio_assistant.agent import AgentConfig, config_version
    from emporio_assistant.anthropic_adapter import AnthropicGenerator, GenerationSettings

    class Probe:
        mode = "live"
        describe = AnthropicGenerator.describe
        settings = GenerationSettings()

    assert config_version(Probe(), AgentConfig(max_model_calls=4)) == "30baf5c5f663"
