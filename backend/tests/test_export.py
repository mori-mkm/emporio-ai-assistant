"""Conversation export (offline, synthetic report)."""

import json

import pytest

from emporio_assistant.export import ExportError, export
from emporio_assistant.runner import DEFAULT_SCENARIOS

SCENARIOS = json.loads(DEFAULT_SCENARIOS.read_text(encoding="utf-8"))
IDS = ["DEV-009", "DEV-013", "DEV-021"]


def report(**changes):
    specs = {s["id"]: s for s in SCENARIOS["scenarios"]}
    base = {
        "run_id": "20990101T000000Z-abcdef",
        "execution": "paid_api",
        "simulated": False,
        "provider": "anthropic",
        "model": "claude-haiku-5-5",
        "config_version": "cfg",
        "configuration": {"generator": {}, "policy_context": {"mode": "full"}},
        "git": {"commit": "c" * 40},
        "data_generations": ["gen"],
        "scenarios": [
            {
                "id": sid,
                "turns": [
                    {
                        "status": "answered",
                        "text": f"reply {sid} {n}",
                        "sources": [{"section": "4.1"}],
                        "trace": [{"step": "tool", "result": "TRACE-ONLY"}],
                        "op_ids": ["OP-ONLY"],
                    }
                    for n, _ in enumerate(specs[sid]["turns"])
                ],
            }
            for sid in IDS
        ],
    }
    return {**base, **changes}


def test_export_keeps_observed_turns_and_drops_internal_data():
    out = export(report(), SCENARIOS, IDS)
    assert "reply DEV-013 1" in out and "§4.1" in out and "`20990101T000000Z-abcdef`" in out
    for spec in (s for s in SCENARIOS["scenarios"] if s["id"] in IDS):
        for turn in spec["turns"]:
            assert turn["user"] in out
            for items in turn["expect"].values():
                assert all(item not in out for item in items)  # no expectations
    assert "TRACE-ONLY" not in out and "OP-ONLY" not in out


@pytest.mark.parametrize(
    "changes",
    [
        {"suite": "validation"},
        {"run_id": "x-validation"},
        {"simulated": True, "execution": "simulated"},
        {"execution": "local_inference"},
    ],
)
def test_export_refuses_validation_simulated_and_local_reports(changes):
    with pytest.raises(ExportError):
        export(report(**changes), SCENARIOS, IDS)


def test_export_needs_three_to_five_scenarios():
    with pytest.raises(ExportError):
        export(report(), SCENARIOS, IDS[:2])
