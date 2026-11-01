"""Development scenario contract (EMP-6): structural rules and the reference check."""

import json
import sqlite3
from pathlib import Path

import pytest

from emporio_assistant.generations import active_generation
from emporio_assistant.prepare_data import main as prepare
from emporio_assistant.scenarios import main, validate_references, validate_structure

SCENARIOS = Path(__file__).resolve().parents[2] / "evals" / "development_scenarios.json"


@pytest.fixture
def data() -> dict:
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))


def test_versioned_set_is_structurally_valid(data):
    assert validate_structure(data) == []
    assert main(["--scenarios", str(SCENARIOS)]) == 0


def test_duplicate_id_is_rejected(data):
    data["scenarios"][1]["id"] = "DEV-001"
    assert any("duplicate scenario ids" in e for e in validate_structure(data))


def test_unknown_smoke_reference_is_rejected(data):
    data["smoke_suite"][0] = "DEV-099"
    assert any("unknown scenarios: ['DEV-099']" in e for e in validate_structure(data))


@pytest.mark.parametrize(
    "expect, message",
    [({}, "no pass criteria"), ({"must_not_claim": ["x"]}, "no positive expectation")],
)
def test_turn_without_pass_criteria_is_rejected(data, expect, message):
    data["scenarios"][0]["turns"][0]["expect"] = expect
    assert any(message in e for e in validate_structure(data))


def test_stored_assistant_reply_or_result_is_rejected(data):
    data["scenarios"][0]["turns"][0]["assistant"] = "invented reply"
    data["scenarios"][1]["result"] = "PASS"
    errors = validate_structure(data)
    assert any("exactly 'user' and 'expect'" in e for e in errors)
    assert any("'result'" in e for e in errors)


def test_wrong_money_value_is_rejected(data):
    check = next(c for c in data["scenarios"][6]["checks"] if c["type"] == "discounted_price")
    check["expected_cents"] = 196717
    assert any("expected_cents" in e for e in validate_structure(data))


def _one_scenario(generation, check: dict) -> dict:
    connection = sqlite3.connect(generation.catalog.as_uri() + "?mode=ro", uri=True)
    hashes = dict(connection.execute("SELECT role, sha256 FROM source_files"))
    connection.close()
    hashes["manual"] = json.loads(generation.policies.read_text(encoding="utf-8"))["source"][
        "sha256"
    ]
    scenario = {
        "id": "DEV-001",
        "references": {"products": [1001], "categories": [], "promotions": [], "manual": []},
        "checks": [check],
    }
    return {"data_version": {"sources_sha256": hashes}, "scenarios": [scenario]}


def test_reference_check_uses_the_active_generation(csv_dir, manual, tmp_path):
    out = tmp_path / "derived"
    assert (
        prepare(["--csv-dir", str(csv_dir), "--manual", str(manual), "--output-dir", str(out)]) == 0
    )
    generation = active_generation(out)
    good = {"type": "product", "product_id": 1001, "price_cents": 123450, "status": "active"}
    assert validate_references(_one_scenario(generation, good), out) == []

    wrong = _one_scenario(generation, {**good, "price_cents": 123400})
    wrong["data_version"]["sources_sha256"]["products"] = "0" * 64
    errors = validate_references(wrong, out)
    assert any("price_cents is 123450, not 123400" in e for e in errors)
    assert any("products hash differs" in e for e in errors)


def test_reference_check_without_data_is_reported_not_passed(tmp_path, capsys):
    assert main(["--scenarios", str(SCENARIOS), "--data-dir", str(tmp_path)]) == 1
    assert "References: NOT CHECKED" in capsys.readouterr().out
