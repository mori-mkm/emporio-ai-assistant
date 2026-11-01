"""Offline scenario runner (EMP-7): drives the scenario turns through an injected executor.

It records what actually ran. It does not grade, does not copy expected answers into the run
and, with the built-in executor, calls no model: every run file says SIMULATED.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .budget import BudgetError
from .scenarios import validate_structure

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIOS = REPO / "evals" / "development_scenarios.json"
DEFAULT_OUT = REPO / "evals" / "results"  # git-ignored

# (scenario_id, history, user_message) -> assistant text, or a dict with "text" plus turn
# diagnostics (status, model_calls, tools_used, op_ids). History is per scenario only.
Respond = Callable[[str, list[dict], str], str | dict]


class BatchStopped(BudgetError):
    """A failure that must stop the batch (budget, hold, uncertain usage, provider error)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Executor:
    name: str
    simulated: bool
    provider: str | None
    model: str | None
    config_version: str
    respond: Respond
    configuration: dict | None = None  # agent + model settings recorded in the report
    ledger_report: Callable[[str], dict] | None = None  # run_id -> ledger view after the run
    trace: bool = False  # per-turn model/tool steps recorded


REPORT_FORMAT = 2  # 1 = no format field: no sources, dropped ids or trace per turn
NOT_RECORDED = "not_recorded"
EVIDENCE_FIELDS = (
    "status", "text", "sources", "dropped_citations", "dropped_sections", "tools_used",
    "op_ids", "data_generation", "trace", "trace_complete",
)  # fmt: skip


def turn_evidence(turn: dict) -> dict:
    """A turn record of any report format, with absent fields marked, never filled in."""
    return {name: turn.get(name, NOT_RECORDED) for name in EVIDENCE_FIELDS}


def new_run_id(simulated: bool) -> str:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    return run_id + ("-simulated" if simulated else "")


def service_executor(service, run_id: str, trace: bool = False) -> Executor:
    """Scenarios through AssistantService: the same sessions, agent, tools and budget path as
    the API. One fresh session per scenario; provider/budget failures stop the whole run.
    `trace` records the ordered model/tool steps of each turn (development evaluation only)."""
    from .assistant import PublicError

    sessions: dict[str, str] = {}

    def respond(scenario_id: str, _history: list[dict], user: str) -> dict:
        if scenario_id not in sessions:
            sessions[scenario_id] = service.start_session(run_id=run_id).id
        diagnostics: dict = {}
        try:
            reply = service.send(
                sessions[scenario_id], user, secrets.token_hex(8), scenario_id, diagnostics, trace
            )
        except PublicError as error:
            failure = BatchStopped(error.code) if error.stops_batch else error
            if "trace" in diagnostics:  # steps up to the failure, in order; never complete
                failure.partial = {"trace": diagnostics["trace"], "trace_complete": False}
            raise failure from None
        if "trace" in diagnostics:
            diagnostics["trace_complete"] = not any(
                s.get("truncated") for s in diagnostics["trace"]
            )
        return {"text": reply["reply"]["text"], **diagnostics}

    generator = service.agent.generator
    describe = getattr(generator, "describe", dict)()
    ledger = getattr(generator, "ledger", None)
    return Executor(
        name=f"assistant-service-{service.mode}",
        simulated=service.mode != "live",
        provider=describe.get("provider"),
        model=describe.get("model"),
        config_version=service.agent.version,
        respond=respond,
        configuration={"generator": describe, "agent": asdict(service.agent.config)},
        ledger_report=ledger.run_report if ledger is not None else None,
        trace=trace,
    )


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
    run_id: str | None = None,
    max_run_s: float | None = None,
) -> Path:
    data = json.loads(scenarios_path.read_text(encoding="utf-8"))
    errors = validate_structure(data)
    if errors:
        raise ValueError(f"scenario file is invalid ({len(errors)} errors)")
    chosen = select(data, ids, smoke)
    started_at = datetime.now(UTC)
    run_id = run_id or new_run_id(executor.simulated)
    run_started = time.monotonic()

    deadline = None if max_run_s is None else run_started + max_run_s

    records = []
    stopped = None
    for scenario in chosen:
        history: list[dict] = []  # fresh per scenario: no state crosses scenarios
        record = {"id": scenario["id"], "turns_total": len(scenario["turns"]), "turns": []}
        begin = time.monotonic()
        record["error"] = None
        for turn in scenario["turns"]:
            # Checked before every turn: no new turn (or paid call) starts after the deadline.
            # A turn already running is bounded by the agent's own turn limit instead.
            if deadline is not None and time.monotonic() >= deadline:
                stopped = "run time limit"
                break
            turn_start = time.monotonic()
            partial: dict = {}  # trace steps recorded before a failure, if any
            try:
                out = executor.respond(scenario["id"], list(history), turn["user"])
            except BudgetError as error:
                record["error"] = getattr(error, "code", type(error).__name__)
                partial = getattr(error, "partial", {})
                stopped = f"batch stopped: {record['error']}"
            except Exception as error:  # recorded per scenario; the run continues
                record["error"] = getattr(error, "code", type(error).__name__)
                partial = getattr(error, "partial", {})
            if record["error"]:
                record["turns"].append(
                    {
                        "status": "error",
                        "error": record["error"],
                        **partial,
                        "duration_ms": _ms(turn_start),
                    }
                )
                break
            out = out if isinstance(out, dict) else {"text": out, "status": None}
            record["turns"].append({**out, "duration_ms": _ms(turn_start)})
            history += [
                {"role": "user", "content": turn["user"]},
                {"role": "assistant", "content": out["text"]},
            ]
        record["turns_executed"] = sum(t["status"] != "error" for t in record["turns"])
        record["turns_answered"] = sum(t["status"] == "answered" for t in record["turns"])
        record["duration_ms"] = _ms(begin)
        if record["turns"] or not stopped:
            records.append(record)
        if stopped:
            break

    not_run = [s["id"] for s in chosen if s["id"] not in {r["id"] for r in records}]
    report = {
        "report_format": REPORT_FORMAT,
        "run_id": run_id,
        "simulated": executor.simulated,
        "trace_enabled": executor.trace,
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
        "configuration": executor.configuration,
        "git": _git_state(),
        "scenario_contract_version": data["contract_version"],
        "data_version": data["data_version"],  # scenarios were written against this data
        "data_generations": sorted(
            {t["data_generation"] for r in records for t in r["turns"] if t.get("data_generation")}
        ),  # what the turns actually used
        "selection": "smoke" if smoke else (ids or "all"),
        "started_at": started_at.isoformat(timespec="seconds"),
        "stopped_early": stopped,
        "max_run_seconds": max_run_s,
        "duration_ms": _ms(run_started),
        "scenarios": records,
        "not_run": not_run,
    }
    if executor.ledger_report is not None:
        try:
            report["ledger"] = executor.ledger_report(run_id)
        except BudgetError as error:
            report["ledger"] = {"error": str(error)}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


def _git_state() -> dict:
    """Commit and whether tracked/untracked files differ from it (paths are not recorded)."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=10
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    if head.returncode or status.returncode:
        return {"commit": None, "dirty": None}
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip())}


PILOT_CAP_USD = Decimal("0.25")  # hard ceiling for a smoke pilot batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run development scenarios.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--smoke", action="store_true", help="run the smoke suite")
    group.add_argument("--ids", help="comma-separated ids, e.g. DEV-001,DEV-004")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--executor",
        choices=["placeholder", "simulated", "live"],
        default="placeholder",
        help="placeholder: no service; simulated: full service with the simulated provider; "
        "live: real Claude calls (paid, needs --confirm-paid-run)",
    )
    parser.add_argument("--confirm-paid-run", action="store_true")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="record each turn's model/tool steps (validated arguments, public tool results)",
    )
    parser.add_argument("--batch-cap-usd", type=Decimal, default=None)
    parser.add_argument("--max-model-calls", type=int, default=4, help="per turn (1-5)")
    parser.add_argument(
        "--max-run-seconds",
        type=float,
        default=900.0,
        help="no new turn starts after this; a running turn stops at the agent's turn limit",
    )
    args = parser.parse_args(argv)
    ids = args.ids.split(",") if args.ids else None
    try:
        if args.executor == "placeholder":
            executor, run_id = simulated_executor(), None
        else:
            executor, run_id = _service_executor(args)
        path = run(
            executor,
            scenarios_path=args.scenarios,
            ids=ids,
            smoke=args.smoke,
            out_dir=args.out_dir,
            run_id=run_id,
            max_run_s=args.max_run_seconds,
        )
    except (OSError, ValueError) as error:
        print(f"FAILED: {error}")
        return 1
    label = "SIMULATED run" if executor.simulated else "LIVE run (real model calls)"
    print(f"{label} written to {path} (nothing graded)")
    return 0


def _service_executor(args) -> tuple[Executor, str]:
    from dataclasses import replace as with_changes

    from .agent import AgentConfig
    from .anthropic_adapter import AdapterConfigError
    from .assistant import Settings, build_service

    if not 1 <= args.max_model_calls <= 5:
        raise ValueError("--max-model-calls must be between 1 and 5")
    settings = Settings.from_env()
    live = args.executor == "live"
    if live:
        if not args.confirm_paid_run:
            raise ValueError("a live run makes paid calls: add --confirm-paid-run")
        cap = args.batch_cap_usd
        if cap is None or not Decimal(0) < cap <= PILOT_CAP_USD:
            raise ValueError(f"--batch-cap-usd is required, > 0 and <= {PILOT_CAP_USD}")
        cap_micro = int(cap * 1_000_000)
    else:
        cap_micro = None
    settings = with_changes(settings, mode="live" if live else "simulated")
    config = AgentConfig(max_model_calls=args.max_model_calls)
    try:
        service = build_service(settings, config=config, run_cap_micro_usd=cap_micro)
    except AdapterConfigError as error:
        raise ValueError(str(error)) from None
    run_id = new_run_id(not live)
    return service_executor(service, run_id, trace=args.trace), run_id


if __name__ == "__main__":
    raise SystemExit(main())
