"""Offline runner (EMP-7): simulated, records only what ran, never grades."""

import json

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
        assert set(record["outputs"]) == {"[SIMULATED] No model was called."}
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
    assert report["stopped_early"] == "budget: InsufficientBudget"


def test_cli_rejects_unknown_ids(tmp_path, capsys):
    assert main(["--ids", "DEV-999", "--out-dir", str(tmp_path)]) == 1
    assert "unknown scenario ids" in capsys.readouterr().out
