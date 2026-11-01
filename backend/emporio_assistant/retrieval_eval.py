"""Offline retrieval evaluation (EMP-12): BM25-selected manual sections vs the manual
references written in the development scenarios.

Kept apart from `policy_context`: the retriever receives only the conversation so far (the
current user message and earlier ones), never scenario ids, later turns, references or the
rubric. Only scenarios with manual references are scored; catalog-only or out-of-scope ones
are listed as excluded instead of counted as hits. References are per scenario, not per turn,
and say where the evidence is, not the complete set needed for a good answer: coverage is a
proxy, not proof that the selected context is sufficient. No model is called.

    python -m emporio_assistant.retrieval_eval [--data-dir data\\derived]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .policy_context import BM25Policy
from .runner import DEFAULT_OUT, DEFAULT_SCENARIOS
from .tools import DEFAULT_DATA_DIR, CatalogSnapshot, ToolError


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline BM25 retrieval evaluation.")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.scenarios.read_text(encoding="utf-8"))
        snapshot = CatalogSnapshot(args.data_dir)
    except (OSError, ValueError, ToolError) as error:
        print(f"FAILED: {error}")
        return 1
    with snapshot:
        report = evaluate(data["scenarios"], snapshot, BM25Policy())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = args.out_dir / f"retrieval-bm25-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"OFFLINE retrieval evaluation written to {path} (no model called)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
