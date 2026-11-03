"""Retrieval evaluation (EMP-12): selected manual sections vs manual references.

BM25 is offline. `--policy embeddings` is paid (development budget, guarded calls): it
builds or reuses the vector index, then embeds each turn's query, and reports index and
query cost/time apart. No answer model is called in either case.

Two methods, both kept apart from `policy_context` (the retriever receives only the
conversation so far, never scenario ids, later turns, references or the rubric):
- `by-turn` (default): the auxiliary key `evals/policy_references_by_turn.json` says, per
  turn, which policy evidence the expected answer needs (groups of equivalent sections),
  which sections only support it, and whether the turn is scored at all. Catalog-only or
  limit-only turns are not applicable: neither hit nor miss. The key is validated strictly
  against the scenarios and the active manual; any mismatch stops the run.
- `scenario-aggregate` (first method, kept to reproduce the earlier 0.38 proxy): the
  scenario's manual references applied to every turn.
Section coverage says whether the evidence was in the context, not whether an answer would be
correct or faithful.

    python -m emporio_assistant.retrieval_eval [--data-dir data\\derived] [--method by-turn]
        [--policy bm25|embeddings --confirm-paid-run --batch-cap-usd 0.02]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
import statistics
from collections import Counter
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .policy_context import BM25Policy
from .runner import DEFAULT_OUT, DEFAULT_SCENARIOS
from .tools import DEFAULT_DATA_DIR, CatalogSnapshot, ToolError

EVAL_VERSION = "by-turn-1"
DEFAULT_REFERENCES = DEFAULT_SCENARIOS.parent / "policy_references_by_turn.json"
NEEDS = {"catalog", "policy", "application_limit"}
SUPPORT_ROLES = {"service_guideline", "tool_rule", "related"}
BASIS = {"manual", "turn_message", "history", "expectation"}  # never model answers or traces
TURN_KEYS = {"scenario", "turn", "user_sha12", "needs", "required", "support",
             "application_limits", "scored", "rationale", "basis"}  # fmt: skip


class ReferenceKeyError(ValueError):
    """The by-turn key does not match the scenarios or the manual; lists every problem."""


def user_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load_references(
    key: dict, scenarios_doc: dict, section_ids: set[str] | None, policies_sha: str | None
) -> dict[tuple[str, int], dict]:
    """Checks the key and returns {(scenario id, turn number): entry}. `section_ids` /
    `policies_sha` None skips the manual checks (structure-only, e.g. in a clean clone)."""
    problems: list[str] = []
    turns = {(s["id"], n): t["user"] for s in scenarios_doc["scenarios"]
             for n, t in enumerate(s["turns"], start=1)}  # fmt: skip
    if not isinstance(key.get("version"), str) or not key["version"]:
        problems.append("missing version")
    if key.get("scenario_contract_version") != scenarios_doc.get("contract_version"):
        problems.append("scenario contract version differs from the scenarios file")
    if set(key.get("basis_allowed", [])) != BASIS:
        problems.append(f"basis_allowed must be {sorted(BASIS)}")
    curated = key.get("curated_against") or {}
    if policies_sha is not None and curated.get("policies_sha256") != policies_sha:
        problems.append("key was curated against different policies (sha256 differs)")
    entries: dict[tuple[str, int], dict] = {}
    for position, entry in enumerate(key.get("turns", [])):
        if not isinstance(entry, dict):
            problems.append(f"turns[{position}]: not an object")
            continue
        where = f"turns[{position}] {entry.get('scenario')} t{entry.get('turn')}"
        if set(entry) != TURN_KEYS:
            problems.append(f"{where}: fields must be {sorted(TURN_KEYS)}")
            continue
        ref = (entry["scenario"], entry["turn"])
        if ref in entries:
            problems.append(f"{where}: duplicate entry")
            continue
        if ref not in turns:
            problems.append(f"{where}: no such scenario turn")
            continue
        entries[ref] = entry
        if entry["user_sha12"] != user_digest(turns[ref]):
            problems.append(f"{where}: user message differs from the scenarios file")
        needs = entry["needs"]
        if not needs or len(set(needs)) != len(needs) or not set(needs) <= NEEDS:
            problems.append(f"{where}: needs must be distinct values of {sorted(NEEDS)}")
        seen: set[str] = set()
        for group in entry["required"]:
            options = group.get("any_of") if isinstance(group, dict) else None
            if (not isinstance(options, list) or not options or set(group) != {"any_of", "covers"}
                    or not str(group["covers"]).strip()):  # fmt: skip
                problems.append(f"{where}: required groups need any_of (non-empty) and covers")
                continue
            for section in options:
                if section in seen:
                    problems.append(f"{where}: section {section} repeated in required")
                seen.add(section)
        support = [s.get("section") for s in entry["support"]]
        for item in entry["support"]:
            if item.get("role") not in SUPPORT_ROLES or set(item) != {"section", "role", "note"}:
                problems.append(f"{where}: support needs section, role {sorted(SUPPORT_ROLES)}"
                                " and note")  # fmt: skip
        if len(set(support)) != len(support) or seen & set(support):
            problems.append(f"{where}: support repeats a section or a required section")
        if section_ids is not None:
            unknown = sorted((seen | set(support)) - section_ids)
            if unknown:
                problems.append(f"{where}: unknown manual sections {unknown}")
        if entry["scored"] is not bool(entry["required"]):
            problems.append(f"{where}: scored must be true exactly when required is not empty")
        if ("policy" in needs) is not bool(entry["required"]):
            problems.append(f"{where}: needs 'policy' exactly when required is not empty")
        if ("application_limit" in needs) is not bool(entry["application_limits"]):
            problems.append(f"{where}: needs 'application_limit' exactly when limits are listed")
        basis = entry["basis"]
        if not basis or not set(basis) <= BASIS or ("history" in basis and entry["turn"] == 1):
            problems.append(f"{where}: basis must be values of {sorted(BASIS)}; no history at t1")
        if not str(entry["rationale"]).strip():
            problems.append(f"{where}: empty rationale")
    missing = sorted(set(turns) - set(entries))
    if missing:
        problems.append(f"missing turns: {[f'{s} t{n}' for s, n in missing]}")
    if problems:
        raise ReferenceKeyError("; ".join(problems))
    return entries


def index_meta(path: Path) -> dict:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return dict(db.execute("SELECT key, value FROM meta").fetchall())


def evaluate(scenarios: list[dict], snapshot: CatalogSnapshot, policy: BM25Policy) -> dict:
    sections = {s["id"]: s for s in snapshot.policies()["sections"]}
    full_chars = sum(len(s["text"]) for s in sections.values())
    path = policy.index_path(snapshot)
    results, excluded = [], []
    for scenario in scenarios:
        refs = sorted({str(m["section"]) for m in scenario["references"]["manual"]})
        if not refs:
            excluded.append(scenario["id"])
            continue
        history: list[dict] = []
        turns, union = [], set()
        for number, turn in enumerate(scenario["turns"], start=1):
            selection = policy.select(snapshot, history, turn["user"])  # past and current only
            got = set(selection.section_ids)
            union |= got
            covered = [r for r in refs if r in got]
            turns.append(
                {
                    "turn": number,
                    "sections": list(selection.section_ids),
                    "hits": selection.details["hits"],
                    "expanded": selection.details["expanded"],
                    "no_match": selection.details["no_match"],
                    "covered": covered,
                    "recall": round(len(covered) / len(refs), 4),
                    "chars": sum(len(sections[i]["text"]) for i in selection.section_ids),
                    "query_ms": selection.details["query_ms"],
                }
            )
            history += [
                {"role": "user", "content": turn["user"]},
                {"role": "assistant", "content": ""},  # not used by the retriever
            ]
        results.append(
            {
                "id": scenario["id"],
                "category": scenario["category"],
                "language": scenario["language"],
                "references": refs,
                "unknown_references": [r for r in refs if r not in sections],
                "turns": turns,
                "union_recall": round(len([r for r in refs if r in union]) / len(refs), 4),
            }
        )
    all_turns = [t for r in results for t in r["turns"]]
    meta = index_meta(path)

    def mean(values):
        return round(statistics.fmean(values), 4) if values else None

    by_language = {}
    for language in sorted({r["language"] for r in results}):
        subset = [t for r in results if r["language"] == language for t in r["turns"]]
        by_language[language] = {"turns": len(subset), "mean_turn_recall": mean(
            [t["recall"] for t in subset])}  # fmt: skip
    return {
        "kind": "offline retrieval evaluation (no model call)",
        "method": "scenario-aggregate",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_generation": snapshot.id,
        "retriever": policy.describe(),
        "index": {"file": path.name, **meta},
        "summary": {
            "scenarios_scored": len(results),
            "scenarios_excluded_no_manual_reference": len(excluded),
            "turns_scored": len(all_turns),
            "mean_turn_recall": mean([t["recall"] for t in all_turns]),
            "turns_full_coverage": sum(t["recall"] == 1 for t in all_turns),
            "turns_zero_coverage": sum(t["recall"] == 0 for t in all_turns),
            "turns_no_match": sum(t["no_match"] for t in all_turns),
            "mean_scenario_union_recall": mean([r["union_recall"] for r in results]),
            "scenarios_union_full_coverage": sum(r["union_recall"] == 1 for r in results),
            "mean_sections_per_turn": mean([len(t["sections"]) for t in all_turns]),
            "mean_context_chars": mean([t["chars"] for t in all_turns]),
            "full_manual_chars": full_chars,
            "mean_context_ratio": mean([t["chars"] / full_chars for t in all_turns]),
            "mean_query_ms": mean([t["query_ms"] for t in all_turns]),
            "max_query_ms": max((t["query_ms"] for t in all_turns), default=None),
            "by_language": by_language,
        },
        "excluded": excluded,
        "scenarios": results,
    }


def _ratio(numerator: int, denominator: int) -> dict:
    value = round(numerator / denominator, 4) if denominator else None
    return {"numerator": numerator, "denominator": denominator, "value": value}


def evaluate_by_turn(
    scenarios_doc: dict, key: dict, snapshot: CatalogSnapshot, policy, context=None
) -> dict:
    """`context` (embeddings only): the paid-call context; each scenario id is set on it."""
    sections = {s["id"]: s for s in snapshot.policies()["sections"]}
    sha = hashlib.sha256(snapshot.generation.policies.read_bytes()).hexdigest()
    entries = load_references(key, scenarios_doc, set(sections), sha)
    full_chars = sum(len(s["text"]) for s in sections.values())
    path = policy.index_path(snapshot)
    turns = []
    for scenario in scenarios_doc["scenarios"]:
        history: list[dict] = []
        call = None if context is None else replace(context, scenario_id=scenario["id"])
        for number, turn in enumerate(scenario["turns"], start=1):
            # past and current customer messages only: no reference or expectation is sent
            selection = policy.select(snapshot, history, turn["user"], context=call)
            history += [{"role": "user", "content": turn["user"]},
                        {"role": "assistant", "content": ""}]  # fmt: skip
            got = set(selection.section_ids)
            entry = entries[(scenario["id"], number)]
            groups = [{"any_of": g["any_of"], "retrieved": sorted(set(g["any_of"]) & got)}
                      for g in entry["required"]]  # fmt: skip
            support = [s["section"] for s in entry["support"]]
            record = {
                "scenario": scenario["id"],
                "turn": number,
                "category": scenario["category"],
                "language": scenario["language"],
                "needs": entry["needs"],
                "scored": entry["scored"],
                "sections": list(selection.section_ids),
                "hits": selection.details["hits"],
                "expanded": selection.details["expanded"],
                "no_match": selection.details["no_match"],
                "chars": sum(len(sections[i]["text"]) for i in selection.section_ids),
                "query_ms": selection.details["query_ms"],
                "op_ids": list(selection.op_ids),
                "support_retrieved": sorted(set(support) & got),
                "support_total": len(support),
            }
            if entry["scored"]:
                satisfied = sum(bool(g["retrieved"]) for g in groups)
                record.update(required=groups, coverage=_ratio(satisfied, len(groups)))
            turns.append(record)

    scored = [t for t in turns if t["scored"]]
    satisfied = sum(t["coverage"]["numerator"] for t in scored)
    total = sum(t["coverage"]["denominator"] for t in scored)
    values = [t["coverage"]["value"] for t in scored]

    def mean(items):
        return round(statistics.fmean(items), 4) if items else None

    by_language = {}
    for language in sorted({t["language"] for t in scored}):
        subset = [t for t in scored if t["language"] == language]
        by_language[language] = {
            "turns": len(subset),
            "groups": _ratio(sum(t["coverage"]["numerator"] for t in subset),
                             sum(t["coverage"]["denominator"] for t in subset)),
        }  # fmt: skip
    return {
        "kind": "offline retrieval evaluation (no model call)",
        "method": "by-turn",
        "evaluation_version": EVAL_VERSION,
        "references_version": key["version"],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_generation": snapshot.id,
        "policies_sha256": sha,
        "retriever": policy.describe(),
        "index": {"file": path.name, **index_meta(path)},
        "not_measured": "answer correctness or faithfulness; this is section coverage only",
        "summary": {
            "turns_total": len(turns),
            "turns_scored": len(scored),
            "turns_not_applicable": len(turns) - len(scored),
            "not_applicable_by_needs": dict(
                sorted(Counter("+".join(t["needs"]) for t in turns if not t["scored"]).items())
            ),
            "required_groups_retrieved": _ratio(satisfied, total),
            "mean_turn_coverage": {
                "sum": round(sum(values), 4),
                "turns": len(values),
                "value": mean(values),
            },
            "scored_turns_full": sum(v == 1 for v in values),
            "scored_turns_partial": sum(0 < v < 1 for v in values),
            "scored_turns_zero": sum(v == 0 for v in values),
            "scored_turns_no_match": sum(t["no_match"] for t in scored),
            "scored_by_language": by_language,
            "support_retrieved_informational": _ratio(
                sum(len(t["support_retrieved"]) for t in turns),
                sum(t["support_total"] for t in turns),
            ),
            "all_turns_no_match": sum(t["no_match"] for t in turns),
            "mean_sections_per_turn": mean([len(t["sections"]) for t in turns]),
            "mean_context_chars": mean([t["chars"] for t in turns]),
            "full_manual_chars": full_chars,
            "mean_context_ratio": mean([t["chars"] / full_chars for t in turns]),
            "mean_query_ms": mean([t["query_ms"] for t in turns]),
            "max_query_ms": max((t["query_ms"] for t in turns), default=None),
        },  # fmt: skip
        "turns": turns,
    }


EMBEDDINGS_CAP_USD = Decimal("0.05")  # ceiling for one index build + query evaluation


def _paid_costs(ledger, run_id: str, index_ops: list[str]) -> dict:
    """Index build and queries reported apart, from the ledger (read-only)."""
    ops = ledger.run_report(run_id)["operations"]

    def part(items):
        return {
            "operations": len(items),
            "states": dict(Counter(o["state"] for o in items)),
            "input_tokens": sum((o["usage"] or {}).get("input", 0) for o in items),
            "confirmed_micro_usd": sum(o["confirmed_micro_usd"] or 0 for o in items),
            "reserved_micro_usd": sum(o["reserved_micro_usd"] for o in items),
            "duration_ms": sum(o["duration_ms"] or 0 for o in items),
        }

    return {
        "index": part([o for o in ops if o["op_id"] in index_ops]),
        "queries": part([o for o in ops if o["op_id"] not in index_ops]),
    }


def _embeddings_report(args, data: dict, key: dict, snapshot: CatalogSnapshot) -> dict:
    """Paid: builds or reuses the index, then embeds each turn's query (development budget,
    budget-guarded). The query is the conversation so far; references never reach it."""
    from .assistant import Settings
    from .budget import Ledger
    from .embeddings import OpenAIEmbedder
    from .generation import CallContext
    from .policy_context import EmbeddingPolicy
    from .pricing import load_price_table

    settings = Settings.from_env()
    ledger = Ledger(settings.ledger_path)
    embedder = OpenAIEmbedder(ledger, load_price_table(settings.pricing_path))
    policy = EmbeddingPolicy(embedder)
    version = hashlib.sha256(json.dumps(policy.describe(), sort_keys=True).encode()).hexdigest()
    run_id = f"retrieval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    context = CallContext(
        run_id=run_id,
        config_version=f"retrieval-{version[:12]}",
        data_version=snapshot.id,
        budget="development",  # never the validation budget in this experiment
        run_cap_micro_usd=int(args.batch_cap_usd * 1_000_000),
        timeout_s=60.0,
    )
    build = policy.build_index(snapshot, replace(context, scenario_id="policy-index"))
    report = evaluate_by_turn(data, key, snapshot, policy, context=context)
    report.update(
        kind="retrieval evaluation with paid embedding calls (no answer model)",
        execution="paid_api",
        run_id=run_id,
        index_build=build,
        query_cache={"hits": policy.cache_hits, "misses": policy.cache_misses},
        costs=_paid_costs(ledger, run_id, build["op_ids"]),
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval evaluation (BM25 offline; "
                                     "embeddings with paid, budget-guarded calls).")  # fmt: skip
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCES)
    parser.add_argument("--method", choices=["by-turn", "scenario-aggregate"], default="by-turn")
    parser.add_argument("--policy", choices=["bm25", "embeddings"], default="bm25",
                        help="embeddings: paid (needs --confirm-paid-run, --batch-cap-usd, "
                        "by-turn, OPENAI_API_KEY in the process)")  # fmt: skip
    parser.add_argument("--confirm-paid-run", action="store_true")
    parser.add_argument("--batch-cap-usd", type=Decimal, default=None)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    paid = args.policy == "embeddings"
    if paid:
        cap = args.batch_cap_usd
        if not args.confirm_paid_run or args.method != "by-turn":
            print("FAILED: embeddings make paid calls: add --confirm-paid-run (by-turn only)")
            return 1
        if cap is None or not Decimal(0) < cap <= EMBEDDINGS_CAP_USD:
            print(f"FAILED: --batch-cap-usd is required, > 0 and <= {EMBEDDINGS_CAP_USD}")
            return 1
    try:
        data = json.loads(args.scenarios.read_text(encoding="utf-8"))
        key = (json.loads(args.references.read_text(encoding="utf-8"))
               if args.method == "by-turn" else None)  # fmt: skip
        snapshot = CatalogSnapshot(args.data_dir)
    except (OSError, ValueError, ToolError) as error:
        print(f"FAILED: {error}")
        return 1
    try:
        with snapshot:
            if paid:
                load_references(key, data, None, None)  # refuse a bad key before paying
                report = _embeddings_report(args, data, key, snapshot)
            elif key is None:
                report = evaluate(data["scenarios"], snapshot, BM25Policy())
            else:
                report = evaluate_by_turn(data, key, snapshot, BM25Policy())
    except ReferenceKeyError as error:
        print(f"FAILED: invalid by-turn references: {error}")
        return 1
    except Exception as error:  # paid path: budget, provider, index or configuration
        if not paid:
            raise
        print(f"FAILED: {type(error).__name__}: {error} (see budget run/summary)")
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = f"{report['references_version']}-{EVAL_VERSION}-" if key is not None else ""
    path = args.out_dir / f"retrieval-{args.policy}-{label}{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if paid:
        print(json.dumps({"costs": report["costs"], "query_cache": report["query_cache"]},
                         indent=2))  # fmt: skip
        print(f"PAID retrieval evaluation written to {path} (embedding calls, no answer model)")
    else:
        print(f"OFFLINE retrieval evaluation written to {path} (no model called)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
