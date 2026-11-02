"""Export observed development conversations as Markdown examples (EMP-16 preparation).

Copies only the customer messages (from the development scenario file) and the replies and
cited sources recorded in a real run report, plus where they came from. Never copies
expectations, traces, tool results, ledger data or reasoning (reports hold none). Refuses
validation reports and simulated runs.

    python -m emporio_assistant.export --report evals/results/<run_id>.json \
        --ids DEV-011,DEV-019 --out .local/examples.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runner import DEFAULT_SCENARIOS

LIMITS = (3, 5)


class ExportError(ValueError):
    pass


def export(report: dict, scenarios: dict, ids: list[str]) -> str:
    if report.get("suite") == "validation" or str(report.get("run_id", "")).endswith("-validation"):
        raise ExportError("validation reports are never exported")
    if report.get("simulated") or report.get("execution") != "paid_api":
        raise ExportError("only real paid-model runs are exported (no simulated or local runs)")
    if not LIMITS[0] <= len(ids) <= LIMITS[1] or len(set(ids)) != len(ids):
        raise ExportError(f"choose {LIMITS[0]}-{LIMITS[1]} distinct scenarios")
    specs = {s["id"]: s for s in scenarios["scenarios"]}
    records = {r["id"]: r for r in report["scenarios"]}
    generator = (report.get("configuration") or {}).get("generator") or {}
    policy = (report.get("configuration") or {}).get("policy_context") or {}
    git = report.get("git") or {}
    lines = [
        "# Example conversations (observed, development set)",
        "",
        f"- Source run: `{report['run_id']}` ({report.get('started_at')}), real paid API calls",
        f"- Model: {report.get('provider')} / {report.get('model')}, config "
        f"`{report.get('config_version')}`, policy context `{policy.get('mode')}`, "
        f"prompt cache `{(generator.get('prompt_cache') or {}).get('mode', 'off')}`",
        f"- Code: commit `{str(git.get('commit'))[:12]}`, data generation "
        f"{', '.join(report.get('data_generations') or [])}",
        "- Messages are the development scenarios' synthetic customer turns; replies are copied "
        "unchanged from the report. Not graded here; no expected answers included.",
    ]
    for sid in ids:
        spec, record = specs.get(sid), records.get(sid)
        if spec is None or record is None:
            raise ExportError(f"{sid} is not in both the scenario file and the report")
        lines += ["", f"## {sid}: {spec['title']}"]
        for turn, observed in zip(spec["turns"], record["turns"], strict=False):
            lines += ["", f"**Customer:** {turn['user']}", ""]
            if observed.get("status") != "answered":
                lines.append(f"**Assistant:** (turn not answered: {observed.get('status')})")
                continue
            lines.append("**Assistant:**")
            lines.append("")
            lines += [f"> {line}" if line else ">" for line in observed["text"].splitlines()]
            sources = ", ".join(f"§{s['section']}" for s in observed.get("sources") or [])
            if sources:
                lines += ["", f"Sources shown to the customer: {sources}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export observed conversations as Markdown.")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ids", required=True, help="3-5 comma-separated DEV ids")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    args = parser.parse_args(argv)
    try:
        text = export(
            json.loads(args.report.read_text(encoding="utf-8")),
            json.loads(args.scenarios.read_text(encoding="utf-8")),
            args.ids.split(","),
        )
    except (OSError, ExportError, KeyError) as error:
        print(f"FAILED: {error}")
        return 1
    args.out.write_text(text, encoding="utf-8")
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
