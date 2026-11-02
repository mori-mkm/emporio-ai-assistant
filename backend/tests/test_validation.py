"""Reserved-validation path (offline). Only SYNTHETIC placeholders: no reserved case is read,
created or searched for here, and no paid call is made (fake client)."""

import json

import pytest
from conftest import FakeAnthropic, text

from emporio_assistant import anthropic_adapter, freeze, runner
from emporio_assistant.budget import Ledger
from emporio_assistant.scenarios import validate_structure, validate_validation

HEX = "0" * 64
MARKER = "EXPECTATION-MARKER-7Q"


def placeholder(n: int) -> dict:
    return {
        "id": f"VAL-{n:03d}",
        "title": f"Placeholder sintético {n}",
        "category": "scope",
        "tags": ["synthetic-placeholder"],
        "language": "pt-BR",
        "goal": "Synthetic structure check only.",
        "preconditions": [],
        "turns": [{"user": f"PLACEHOLDER SINTÉTICO {n}", "expect": {"inform": [MARKER]}}],
        "references": {"products": [], "categories": [], "promotions": [], "manual": []},
        "checks": [],
        "data_access": {"required": [], "forbidden": []},
        "critical_failures": [],
        "evaluation": "human",
        "smoke": False,
    }


def suite(count: int = 10) -> dict:
    return {
        "suite": "reserved-validation",
        "contract_version": 2,
        "data_version": {
            "sources_sha256": {k: HEX for k in ("categories", "products", "promotions", "manual")}
        },
        "scenarios": [placeholder(n) for n in range(1, count + 1)],
    }


def test_validation_contract_is_separate_from_development():
    assert validate_validation(suite()) == []
    assert validate_structure(suite())  # the development contract refuses it
    dev = json.loads(runner.DEFAULT_SCENARIOS.read_text(encoding="utf-8"))
    assert validate_structure(dev) == [] and validate_validation(dev)
    assert any("VAL-001 to VAL-010" in e for e in validate_validation(suite(9)))
    wrong = suite()
    wrong["scenarios"][0]["id"] = "DEV-001"
    wrong["scenarios"][1]["smoke"] = True
    errors = validate_validation(wrong)
    assert any("must match" in e for e in errors) and any(
        "smoke must be false" in e for e in errors
    )


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def paths(tmp_path, monkeypatch, generation_dir):
    ledger = tmp_path / "ledger.sqlite"
    Ledger.create(ledger, "live")
    monkeypatch.setenv("EMPORIO_LEDGER_PATH", str(ledger))
    monkeypatch.setenv("EMPORIO_DATA_DIR", str(generation_dir))
    monkeypatch.setattr(runner, "_git_state", lambda: {"commit": "f" * 40, "dirty": False})
    out = tmp_path / "validation-out"
    out.mkdir()
    return {
        "ledger": ledger,
        "scenarios": write(tmp_path / "val.json", suite()),
        "out": out,
        "manifest": tmp_path / "freeze.json",
    }


def args(p, *extra):
    return [
        "--suite", "validation", "--executor", "live", "--provider", "anthropic",
        "--prompt-cache", "system-5m", "--confirm-paid-run", "--confirm-validation-run",
        "--batch-cap-usd", "0.10", "--max-model-calls", "4",
        "--scenarios", str(p["scenarios"]), "--out-dir", str(p["out"]),
        "--freeze-manifest", str(p["manifest"]), *extra,
    ]  # fmt: skip


def no_client(monkeypatch):
    def refuse(_timeout):
        raise AssertionError("a client was created")

    monkeypatch.setattr(anthropic_adapter, "_new_client", refuse)


def test_inconsistent_validation_requests_are_refused_before_any_client(paths, monkeypatch, capsys):
    no_client(monkeypatch)
    full = args(paths)
    cases = [
        [a for a in full if a != "--confirm-validation-run"],
        [*full, "--ids", "VAL-001"],
        [a if a != "live" else "simulated" for a in full],
        [a if a != str(paths["out"]) else str(runner.DEFAULT_OUT) for a in full],  # dev tree
        [a if a != str(paths["scenarios"]) else str(runner.DEFAULT_SCENARIOS) for a in full],
        full,  # manifest file missing
    ]
    for case in cases:
        assert runner.main(case) == 1
    output = capsys.readouterr().out
    assert "--confirm-validation-run" in output and "no --smoke or --ids" in output
    assert "private folder" in output
    assert list(paths["out"].iterdir()) == []
    summary = Ledger(paths["ledger"]).summary()
    assert summary["operations_by_state"] == {}


def test_divergent_freeze_manifest_is_refused_with_its_differences(paths, monkeypatch, capsys):
    no_client(monkeypatch)
    assert freeze.main(["write", "--out", str(paths["manifest"]), "--prompt-cache", "off"]) == 0
    assert runner.main(args(paths)) == 1  # frozen with cache off, run asks for system-5m
    output = capsys.readouterr().out
    assert "frozen configuration differs" in output and "config_version" in output
    assert list(paths["out"].iterdir()) == []


def test_freeze_write_refuses_a_dirty_tree_or_a_tracked_path(paths, monkeypatch):
    target = ["--prompt-cache", "system-5m"]
    assert freeze.main(["write", "--out", str(runner.REPO / "freeze.json"), *target]) == 1
    monkeypatch.setattr(runner, "_git_state", lambda: {"commit": "f" * 40, "dirty": True})
    assert freeze.main(["write", "--out", str(paths["manifest"]), *target]) == 1
    assert not paths["manifest"].exists()


def test_divergences_list_changed_paths_and_ignore_the_write_time():
    current = {"config_version": "a", "data": {"generation": "g1"}}
    frozen = {"config_version": "b", "data": {"generation": "g1"}, "written_at": "t"}
    assert freeze.divergences(frozen, current) == ["config_version: frozen 'b' != current 'a'"]
    assert freeze.divergences({**current, "written_at": "t"}, current) == []


def test_frozen_run_uses_the_validation_budget_and_private_output(paths, monkeypatch):
    fake = FakeAnthropic(*[text("[resposta sintética]") for _ in range(10)])
    monkeypatch.setattr(anthropic_adapter, "_new_client", lambda _timeout: fake)
    write_args = [
        "write", "--out", str(paths["manifest"]), "--prompt-cache", "system-5m",
        "--scenarios", str(paths["scenarios"]),
    ]  # fmt: skip
    assert freeze.main(write_args) == 0
    assert runner.main(args(paths)) == 0
    budgets = Ledger(paths["ledger"]).summary()["budgets"]
    assert budgets["validation"]["confirmed_micro_usd"] > 0
    assert budgets["development"]["confirmed_micro_usd"] == 0
    (report_path,) = paths["out"].glob("*-validation.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["suite"] == "validation" and report["configuration"]["budget"] == "validation"
    check = report["configuration"]["freeze_check"]
    assert check["divergences"] == [] and check["config_version"] == report["config_version"]
    assert len(report["scenarios"]) == 10 and report["not_run"] == []
    assert report["fixtures_enabled"] is False  # development fixtures never apply here
    assert MARKER not in json.dumps(report)  # expectations never copied into the report
    assert all(MARKER not in json.dumps(r) for r in fake.requests)  # nor into the prompt
