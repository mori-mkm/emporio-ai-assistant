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
from decimal import Decimal
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


def new_run_id(simulated: bool) -> str:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    return run_id + ("-simulated" if simulated else "")


def service_executor(service, run_id: str) -> Executor:
    """Scenarios through AssistantService: the same sessions, agent, tools and budget path as
    the API. One fresh session per scenario; provider/budget failures stop the whole run."""
    from .assistant import PublicError

    sessions: dict[str, str] = {}

    def respond(scenario_id: str, _history: list[dict], user: str) -> str:
        if scenario_id not in sessions:
            sessions[scenario_id] = service.start_session(run_id=run_id).id
        try:
            reply = service.send(sessions[scenario_id], user, secrets.token_hex(8), scenario_id)
        except PublicError as error:
            if error.stops_batch:
                raise BudgetError(error.code) from None
            raise
        return reply["reply"]["text"]

    describe = getattr(service.agent.generator, "describe", dict)()
    return Executor(
        name=f"assistant-service-{service.mode}",
        simulated=service.mode != "live",
        provider=describe.get("provider"),
        model=describe.get("model"),
        config_version=service.agent.version,
        respond=respond,
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

    records = []
    stopped = None
    for scenario in chosen:
        if max_run_s is not None and time.monotonic() - run_started > max_run_s:
            stopped = "run time limit"
            break
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
            record["error"] = getattr(error, "code", type(error).__name__)
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
    parser.add_argument("--batch-cap-usd", type=Decimal, default=None)
    parser.add_argument("--max-model-calls", type=int, default=4, help="per turn (1-5)")
    parser.add_argument("--max-run-seconds", type=float, default=900.0)
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
    return service_executor(service, run_id), run_id


if __name__ == "__main__":
    raise SystemExit(main())
