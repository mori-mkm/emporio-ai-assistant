"""Validate the development evaluation scenarios (EMP-6).

Two levels, reported separately:
- structure: the contract itself (ids, fields, expectations, smoke suite, coverage, arithmetic).
  Needs only the JSON file, so it runs in a clean clone.
- references: every check against the active data generation. Needs prepared data.

This is not a model runner or a scorer.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .generations import GenerationError, active_generation

SCENARIO_COUNT = 30
ID_PATTERN = re.compile(r"DEV-\d{3}")
# Reserved validation (owner-held, outside the development tree): same per-scenario contract,
# its own ids and size. Nothing here reads or creates those cases.
VALIDATION_SUITE = "reserved-validation"
VALIDATION_COUNT = 10
VALIDATION_ID = re.compile(r"VAL-\d{3}")
CATEGORIES = {"catalog", "pricing", "policy", "continuity", "scope", "security"}
LANGUAGES = {"pt-BR", "en"}
EVALUATION = {"deterministic", "human", "both"}
CAPABILITIES = {"catalog", "policies", "orders", "customers", "write_operations", "other_sessions"}
EXPECT_KEYS = {"inform", "ask", "must_not_claim", "must_not_do", "report_failure"}
POSITIVE_KEYS = {"inform", "ask", "report_failure"}
MIN_MULTI_TURN = 6
SMOKE_SIZE = (5, 8)
# Keys that would mean a stored run or measured outcome; the set holds specifications only.
FORBIDDEN_KEYS = {
    "assistant", "response", "responses", "result", "results", "score", "scores",
    "passed", "accuracy", "latency", "cost", "satisfaction",
}  # fmt: skip
CHECK_FIELDS = {
    "product": {"product_id"},
    "promotion": {"promotion_id", "product_id"},
    "category_without_products": {"category_id"},
    "product_set": {"category_id", "product_ids"},
    "discounted_price": {"base_cents", "discount_basis_points", "expected_cents"},
    "installment": {"total_cents", "installments", "per_installment_cents"},
    "manual_phrase": {"section", "page", "text"},
}
SCENARIO_FIELDS = {
    "id": str, "title": str, "category": str, "tags": list, "language": str, "goal": str,
    "preconditions": list, "turns": list, "references": dict, "checks": list,
    "data_access": dict, "critical_failures": list, "evaluation": str, "smoke": bool,
}  # fmt: skip


def _texts(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def _ints(value: object) -> bool:
    return isinstance(value, list) and all(type(x) is int and x > 0 for x in value)


def _find_forbidden_keys(node: object, path: str, errors: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in FORBIDDEN_KEYS:
                errors.append(f"{path}: key '{key}' looks like a stored run or result")
            _find_forbidden_keys(value, f"{path}.{key}", errors)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _find_forbidden_keys(value, f"{path}[{i}]", errors)


def _discounted(base: int, basis_points: int) -> int:
    """Reference value, computed independently of the production pricing code."""
    exact = Decimal(base) * (Decimal(10000 - basis_points) / Decimal(10000))
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _check_arithmetic(where: str, check: dict, errors: list[str]) -> None:
    if check["type"] == "discounted_price":
        base, bp, expected = (
            check["base_cents"],
            check["discount_basis_points"],
            check["expected_cents"],
        )
        if not (type(base) is int and base > 0 and type(bp) is int and 0 < bp < 10000):
            errors.append(f"{where}: base_cents/discount_basis_points invalid")
        elif type(expected) is not int or expected != _discounted(base, bp):
            errors.append(
                f"{where}: expected_cents {expected!r} != {_discounted(base, bp)} (ROUND_HALF_UP)"
            )
    elif check["type"] == "installment":
        total, n, per = check["total_cents"], check["installments"], check["per_installment_cents"]
        if not all(type(x) is int and x > 0 for x in (total, n, per)):
            errors.append(f"{where}: installment values must be positive integers")
        elif per * n != total:
            errors.append(f"{where}: {n} x {per} != {total} (use an exactly divisible case)")


def _validate_scenario(s: dict, errors: list[str], id_pattern: re.Pattern = ID_PATTERN) -> None:
    sid = s.get("id", "?")
    bad = [n for n, kind in SCENARIO_FIELDS.items() if not isinstance(s.get(n), kind)]
    if bad:
        errors.append(f"{sid}: fields missing or of the wrong type: {bad}")
        return
    if not id_pattern.fullmatch(sid):
        errors.append(f"{sid}: id must match {id_pattern.pattern}")
    for name in ("title", "goal"):
        if not s[name].strip():
            errors.append(f"{sid}: '{name}' is empty")
    if s["category"] not in CATEGORIES:
        errors.append(f"{sid}: unknown category '{s['category']}'")
    if s["language"] not in LANGUAGES:
        errors.append(f"{sid}: unknown language '{s['language']}'")
    if s["evaluation"] not in EVALUATION:
        errors.append(f"{sid}: unknown evaluation '{s['evaluation']}'")
    for name in ("tags", "preconditions", "critical_failures"):
        if not _texts(s[name]):
            errors.append(f"{sid}: '{name}' must be a list of non-empty strings")
    if not s["tags"]:
        errors.append(f"{sid}: needs at least one tag")

    if not s["turns"]:
        errors.append(f"{sid}: no user turns")
    for i, turn in enumerate(s["turns"], start=1):
        where = f"{sid} turn {i}"
        if not isinstance(turn, dict) or set(turn) != {"user", "expect"}:
            errors.append(f"{where}: must have exactly 'user' and 'expect'")
            continue
        if not isinstance(turn["user"], str) or not turn["user"].strip():
            errors.append(f"{where}: empty user message")
        expect = turn["expect"]
        if not isinstance(expect, dict) or not expect:
            errors.append(f"{where}: no pass criteria")
            continue
        unknown = set(expect) - EXPECT_KEYS
        if unknown:
            errors.append(f"{where}: unknown expectation keys {sorted(unknown)}")
        for key, value in expect.items():
            if not _texts(value) or not value:
                errors.append(f"{where}: '{key}' must be a non-empty list of strings")
        if not POSITIVE_KEYS & set(expect):
            errors.append(f"{where}: no positive expectation (inform/ask/report_failure)")

    refs = s["references"]
    if set(refs) != {"products", "categories", "promotions", "manual"}:
        errors.append(f"{sid}: references need products, categories, promotions, manual")
        return
    for name in ("products", "categories", "promotions"):
        if not isinstance(refs[name], list) or (refs[name] and not _ints(refs[name])):
            errors.append(f"{sid}: references.{name} must be positive integer ids")
    manual = refs["manual"]
    if not isinstance(manual, list) or not all(
        isinstance(m, dict)
        and set(m) == {"section", "page"}
        and isinstance(m["section"], str)
        and type(m["page"]) is int
        and m["page"] > 0
        for m in manual
    ):
        errors.append(f"{sid}: references.manual entries need section (str) and page (int)")
        manual = []
    sections = {m["section"] for m in manual}

    access = s["data_access"]
    if set(access) != {"required", "forbidden"} or not all(
        isinstance(access[k], list) and set(access[k]) <= CAPABILITIES for k in access
    ):
        errors.append(f"{sid}: data_access needs required/forbidden from {sorted(CAPABILITIES)}")
    elif set(access["required"]) & set(access["forbidden"]):
        errors.append(f"{sid}: a capability is both required and forbidden")

    if s["evaluation"] in {"deterministic", "both"} and not s["checks"]:
        errors.append(f"{sid}: '{s['evaluation']}' evaluation needs at least one check")
    for j, check in enumerate(s["checks"], start=1):
        where = f"{sid} check {j}"
        kind = check.get("type") if isinstance(check, dict) else None
        if kind not in CHECK_FIELDS:
            errors.append(f"{where}: unknown check type {kind!r}")
            continue
        missing = CHECK_FIELDS[kind] - set(check)
        if missing:
            errors.append(f"{where}: missing {sorted(missing)}")
            continue
        _check_arithmetic(where, check, errors)
        if "product_id" in check and check["product_id"] not in refs["products"]:
            errors.append(f"{where}: product {check['product_id']} not in references")
        for pid in check.get("product_ids", []):
            if pid not in refs["products"]:
                errors.append(f"{where}: product {pid} not in references")
        if kind == "promotion" and check["promotion_id"] not in refs["promotions"]:
            errors.append(f"{where}: promotion {check['promotion_id']} not in references")
        if "category_id" in check and check["category_id"] not in refs["categories"]:
            errors.append(f"{where}: category {check['category_id']} not in references")
        if kind == "manual_phrase" and check["section"] not in sections:
            errors.append(f"{where}: section {check['section']} not in references.manual")


def _common_errors(data: dict) -> list[str]:
    errors: list[str] = []
    _find_forbidden_keys(data, "$", errors)
    if type(data.get("contract_version")) is not int:
        errors.append("contract_version must be an integer")
    version = data.get("data_version")
    hashes = version.get("sources_sha256") if isinstance(version, dict) else None
    if not isinstance(hashes, dict) or set(hashes) != {
        "categories",
        "products",
        "promotions",
        "manual",
    }:
        errors.append("data_version.sources_sha256 needs categories, products, promotions, manual")
    elif not all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes.values()):
        errors.append("data_version.sources_sha256 values must be SHA-256 hex digests")
    return errors


def validate_validation(data: object) -> list[str]:
    """Reserved validation contract: VAL-001..VAL-010, the same per-scenario fields, checks and
    expectation keys as the development set, no smoke suite. Structure only."""
    if not isinstance(data, dict):
        return ["top level must be an object"]
    errors = _common_errors(data)
    if data.get("suite") != VALIDATION_SUITE:
        errors.append(f"suite must be '{VALIDATION_SUITE}'")
    if "smoke_suite" in data:
        errors.append("validation files have no smoke_suite")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not all(isinstance(s, dict) for s in scenarios):
        return errors + ["scenarios must be a list of objects"]
    ids = [s.get("id") for s in scenarios]
    expected = {f"VAL-{n:03d}" for n in range(1, VALIDATION_COUNT + 1)}
    if len(ids) != VALIDATION_COUNT or set(ids) != expected:
        errors.append(f"scenario ids must be exactly VAL-001 to VAL-{VALIDATION_COUNT:03d}")
    for s in scenarios:
        _validate_scenario(s, errors, VALIDATION_ID)
        if s.get("smoke") is not False:
            errors.append(f"{s.get('id', '?')}: smoke must be false in validation")
    return errors


def validate_structure(data: object) -> list[str]:
    """Return contract errors; an empty list means the set is structurally valid."""
    if not isinstance(data, dict):
        return ["top level must be an object"]
    errors = _common_errors(data)
    if "suite" in data:
        errors.append("the development set has no 'suite' field (validation files do)")
    if not isinstance(data.get("smoke_rationale"), str) or not data["smoke_rationale"].strip():
        errors.append("smoke_rationale is required")

    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not all(isinstance(s, dict) for s in scenarios):
        return errors + ["scenarios must be a list of objects"]
    if len(scenarios) != SCENARIO_COUNT:
        errors.append(f"expected {SCENARIO_COUNT} scenarios, found {len(scenarios)}")
    ids = [s.get("id") for s in scenarios]
    duplicates = sorted({i for i in ids if ids.count(i) > 1}, key=str)
    if duplicates:
        errors.append(f"duplicate scenario ids: {duplicates}")
    expected_ids = {f"DEV-{n:03d}" for n in range(1, SCENARIO_COUNT + 1)}
    if set(ids) != expected_ids and not duplicates:
        errors.append("scenario ids must be exactly DEV-001 to DEV-030")
    for s in scenarios:
        _validate_scenario(s, errors)

    smoke = data.get("smoke_suite")
    if not isinstance(smoke, list) or len(set(smoke)) != len(smoke):
        errors.append("smoke_suite must be a list of distinct ids")
    else:
        unknown = sorted(set(smoke) - set(ids), key=str)
        if unknown:
            errors.append(f"smoke_suite references unknown scenarios: {unknown}")
        if not SMOKE_SIZE[0] <= len(smoke) <= SMOKE_SIZE[1]:
            errors.append(f"smoke_suite must have {SMOKE_SIZE[0]}-{SMOKE_SIZE[1]} scenarios")
        flagged = {s.get("id") for s in scenarios if s.get("smoke") is True}
        if flagged != set(smoke):
            errors.append("smoke flags on scenarios do not match smoke_suite")

    present = {s.get("category") for s in scenarios}
    if present != CATEGORIES:
        errors.append(f"categories not covered: {sorted(CATEGORIES - present, key=str)}")
    multi = sum(1 for s in scenarios if isinstance(s.get("turns"), list) and len(s["turns"]) > 1)
    if multi < MIN_MULTI_TURN:
        errors.append(f"only {multi} multi-turn scenarios; need at least {MIN_MULTI_TURN}")
    return errors


def _squash(text: str) -> str:
    return " ".join(text.split())


def validate_references(data: dict, data_dir: Path) -> list[str]:
    """Check every reference and check against the active generation. Raises GenerationError."""
    generation = active_generation(data_dir)
    policies = json.loads(generation.policies.read_text(encoding="utf-8"))
    sections = {s["id"]: s for s in policies["sections"]}
    errors: list[str] = []

    expected = data["data_version"]["sources_sha256"]
    if policies["source"]["sha256"] != expected["manual"]:
        errors.append("data_version: manual hash differs from the active generation")

    connection = sqlite3.connect(generation.catalog.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for role, digest in connection.execute("SELECT role, sha256 FROM source_files"):
            if role in expected and expected[role] != digest:
                errors.append(f"data_version: {role} hash differs from the active generation")

        def row(table: str, key: str, value: int) -> sqlite3.Row | None:
            return connection.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone()

        for s in data["scenarios"]:
            sid, refs = s["id"], s["references"]
            for table, key, name in (
                ("products", "product_id", "products"),
                ("categories", "category_id", "categories"),
                ("promotions", "promotion_id", "promotions"),
            ):
                for value in refs[name]:
                    if row(table, key, value) is None:
                        errors.append(f"{sid}: {name} {value} not found")
            for m in refs["manual"]:
                section = sections.get(m["section"])
                if section is None:
                    errors.append(f"{sid}: manual section {m['section']} not found")
                elif not section["page_start"] <= m["page"] <= section["page_end"]:
                    errors.append(f"{sid}: section {m['section']} is not on page {m['page']}")

            for j, check in enumerate(s["checks"], start=1):
                where, kind = f"{sid} check {j}", check["type"]
                fields: set[str] = set()
                if kind == "product" or (kind == "discounted_price" and "product_id" in check):
                    found = row("products", "product_id", check["product_id"])
                    if found is None:
                        errors.append(f"{where}: product {check['product_id']} not found")
                        continue
                    if kind == "discounted_price" and found["price_cents"] != check["base_cents"]:
                        errors.append(f"{where}: base_cents differs from the product price")
                    fields = {"price_cents", "stock_quantity", "status"}
                elif kind == "promotion":
                    found = row("promotions", "promotion_id", check["promotion_id"])
                    if found is None:
                        errors.append(f"{where}: promotion {check['promotion_id']} not found")
                        continue
                    fields = {"product_id", "discount_basis_points", "is_active"}
                elif kind == "category_without_products":
                    count = connection.execute(
                        "SELECT COUNT(*) FROM products WHERE category_id = ?",
                        (check["category_id"],),
                    ).fetchone()[0]
                    if row("categories", "category_id", check["category_id"]) is None or count:
                        errors.append(f"{where}: category is missing or has products")
                elif kind == "product_set":
                    sql = "SELECT product_id FROM products WHERE category_id = ?"
                    params: list[object] = [check["category_id"]]
                    if "max_price_cents" in check:
                        sql += " AND price_cents <= ?"
                        params.append(check["max_price_cents"])
                    if "name_contains" in check:
                        sql += " AND instr(name, ?) > 0"
                        params.append(check["name_contains"])
                    if check.get("available_only"):
                        sql += " AND status = 'active' AND stock_quantity > 0"
                    actual = {r[0] for r in connection.execute(sql, params)}
                    if actual != set(check["product_ids"]):
                        errors.append(f"{where}: product set differs: data has {sorted(actual)}")
                elif kind == "manual_phrase":
                    section = sections.get(check["section"])
                    if section is None:
                        errors.append(f"{where}: section {check['section']} not found")
                    elif _squash(check["text"]) not in _squash(section["text"]):
                        errors.append(f"{where}: phrase not found in section {check['section']}")
                    elif not section["page_start"] <= check["page"] <= section["page_end"]:
                        errors.append(f"{where}: section {check['section']} not on page")
                for field in fields & set(check):
                    if found[field] != check[field]:
                        errors.append(f"{where}: {field} is {found[field]!r}, not {check[field]!r}")
    finally:
        connection.close()
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, help="prepared data folder (current.json)")
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.scenarios.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAILED: cannot read scenarios: {error}")
        return 1
    errors = validate_structure(data)
    if errors:
        print(f"Structure: FAILED ({len(errors)} errors)", *errors, sep="\n- ")
        return 1
    scenarios = data["scenarios"]
    print(
        f"Structure: OK ({len(scenarios)} scenarios, "
        f"{sum(len(s['turns']) > 1 for s in scenarios)} multi-turn, "
        f"smoke {', '.join(data['smoke_suite'])})"
    )
    if args.data_dir is None:
        print("References: NOT CHECKED (pass --data-dir with prepared data)")
        return 0
    try:
        errors = validate_references(data, args.data_dir)
    except GenerationError as error:
        print(f"References: NOT CHECKED ({error})")
        return 1
    if errors:
        print(f"References: FAILED ({len(errors)} errors)", *errors, sep="\n- ")
        return 1
    checks = sum(len(s["checks"]) for s in scenarios)
    print(f"References: OK ({checks} checks against the active generation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
