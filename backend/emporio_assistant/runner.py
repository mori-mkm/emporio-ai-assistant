"""Offline scenario runner (EMP-7): drives the scenario turns through an injected executor.

It records what actually ran. It does not grade, does not copy expected answers into the run
and, with the built-in executor, calls no model: every run file says SIMULATED.
"""

from __future__ import annotations

import argparse
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .budget import BudgetError
from .scenarios import validate_structure

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIOS = REPO / "evals" / "development_scenarios.json"
DEFAULT_OUT = REPO / "evals" / "results"  # git-ignored

# (scenario_id, history, user_message) -> assistant text. History is per scenario only.
Respond = Callable[[str, list[dict], str], str]


@dataclass(frozen=True)
class Executor:
    name: str
    simulated: bool
    provider: str | None
    model: str | None
    config_version: str
    respond: Respond


def simulated_executor() -> Executor:
    return Executor(
        name="simulated-placeholder",
        simulated=True,
        provider=None,
        model=None,
        config_version="simulated",
        respond=lambda _sid, _history, _user: "[SIMULATED] No model was called.",
    )


def select(data: dict, ids: list[str] | None, smoke: bool) -> list[dict]:
    by_id = {s["id"]: s for s in data["scenarios"]}
    wanted = data["smoke_suite"] if smoke else ids or list(by_id)
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        raise ValueError(f"unknown scenario ids: {unknown}")
    return [by_id[i] for i in wanted]


def run(
    executor: Executor,
    *,
    scenarios_path: Path = DEFAULT_SCENARIOS,
    ids: list[str] | None = None,
    smoke: bool = False,
    out_dir: Path = DEFAULT_OUT,
) -> Path:
    data = json.loads(scenarios_path.read_text(encoding="utf-8"))
    errors = validate_structure(data)
    if errors:
        raise ValueError(f"scenario file is invalid ({len(errors)} errors)")
    chosen = select(data, ids, smoke)
    started_at = datetime.now(UTC)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    if executor.simulated:
        run_id += "-simulated"

    records = []
    stopped = None
    for scenario in chosen:
        history: list[dict] = []  # fresh per scenario: no state crosses scenarios
        record = {"id": scenario["id"], "turns_total": len(scenario["turns"]), "outputs": []}
        begin = time.monotonic()
        try:
            for turn in scenario["turns"]:
                text = executor.respond(scenario["id"], list(history), turn["user"])
                history += [
                    {"role": "user", "content": turn["user"]},
                    {"role": "assistant", "content": text},
                ]
                record["outputs"].append(text)
            record["error"] = None
        except BudgetError as error:
            record["error"] = type(error).__name__
            stopped = f"budget: {type(error).__name__}"
        except Exception as error:  # recorded per scenario; the run continues
            record["error"] = type(error).__name__
        record["turns_executed"] = len(record["outputs"])
        record["duration_ms"] = round((time.monotonic() - begin) * 1000)
        records.append(record)
        if stopped:
            break

    report = {
        "run_id": run_id,
        "simulated": executor.simulated,
        "notice": (
            "SIMULATED run: no model was called; outputs are placeholders, not agent answers."
            if executor.simulated
            else "Model run; outputs are recorded as returned."
        ),
        "graded": False,
        "executor": executor.name,
        "provider": executor.provider,
        "model": executor.model,
        "config_version": executor.config_version,
        "scenario_contract_version": data["contract_version"],
        "data_version": data["data_version"],
        "selection": "smoke" if smoke else (ids or "all"),
        "started_at": started_at.isoformat(timespec="seconds"),
        "stopped_early": stopped,
        "scenarios": records,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run scenarios with the simulated executor.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--smoke", action="store_true", help="run the smoke suite")
    group.add_argument("--ids", help="comma-separated ids, e.g. DEV-001,DEV-004")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    ids = args.ids.split(",") if args.ids else None
    try:
        path = run(
            simulated_executor(),
            scenarios_path=args.scenarios,
            ids=ids,
            smoke=args.smoke,
            out_dir=args.out_dir,
        )
    except (OSError, ValueError) as error:
        print(f"FAILED: {error}")
        return 1
    print(f"SIMULATED run written to {path} (no model called, nothing graded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
