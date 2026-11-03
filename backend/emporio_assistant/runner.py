"""Offline scenario runner (EMP-7): drives the scenario turns through an injected executor.

It records what actually ran. It does not grade, does not copy expected answers into the run
and, with the built-in executor, calls no model: every run file says SIMULATED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .agent import AgentConfig
from .budget import BudgetError
from .fixtures import DEV_028_TEXT, AuxiliarySession, CatalogTimeout, ProductTextInjection
from .scenarios import validate_structure, validate_validation

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
    execution: str = "simulated"  # simulated | local_inference | paid_api
    runtime_report: Callable[[], dict] | None = None  # local model state (version, ollama ps)
    end_scenario: Callable[[str], dict | None] | None = None  # fixture report + cleanup
    fixtures: bool = False  # scenario environments (FIXTURES) are applied


# Scenario preconditions that need a test environment; applied only by service_executor.
FIXTURES: dict[str, Callable] = {
    "DEV-027": AuxiliarySession,
    "DEV-028": partial(ProductTextInjection, 87, DEV_028_TEXT),
    "DEV-029": CatalogTimeout,
}
PERTURBING_KINDS = {"tool_result_perturbation", "simulated_tool_failure"}


def _kind(factory: Callable) -> str:
    return getattr(factory, "func", factory).kind


EXECUTION = {"live": "paid_api", "local": "local_inference", "simulated": "simulated"}
NOTICES = {
    "simulated": "SIMULATED run: no model was called; outputs are placeholders, not agent answers.",
    "local_inference": "Local model run (real inference on this machine, no API cost); outputs "
    "are recorded as returned.",
    "paid_api": "Model run; outputs are recorded as returned.",
}


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


def service_executor(
    service, run_id: str, trace: bool = False, fixtures: dict | None = None
) -> Executor:
    """Scenarios through AssistantService: the same sessions, agent, tools and budget path as
    the API. One fresh session per scenario; provider/budget failures stop the whole run.
    `trace` records the ordered model/tool steps of each turn (development evaluation only).
    `fixtures` (default FIXTURES) builds one environment per listed scenario, used only by
    that scenario's session and discarded when it ends."""
    from .assistant import PublicError

    fixtures = FIXTURES if fixtures is None else fixtures
    sessions: dict[str, object] = {}
    envs: dict[str, object] = {}

    def respond(scenario_id: str, _history: list[dict], user: str) -> dict:
        if scenario_id not in sessions:
            env = fixtures[scenario_id]() if scenario_id in fixtures else None
            if isinstance(env, AuxiliarySession):
                env.prepare(service, run_id)  # the other session exists before this one
            session = service.start_session(run_id=run_id)
            if isinstance(env, AuxiliarySession):
                env.evaluated_started(session)
            sessions[scenario_id] = session
            if env is not None:
                envs[scenario_id] = env
        env = envs.get(scenario_id)
        diagnostics: dict = {}
        try:
            reply = service.send(
                sessions[scenario_id].id,
                user,
                secrets.token_hex(8),
                scenario_id,
                diagnostics,
                trace,
                tool_call=getattr(env, "call", None),
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

    def end_scenario(scenario_id: str) -> dict | None:
        env = envs.pop(scenario_id, None)
        if env is None:
            return None
        if isinstance(env, AuxiliarySession):
            return env.finish(service, sessions.get(scenario_id))
        return env.report()

    generator = service.agent.generator
    describe = getattr(generator, "describe", dict)()
    ledger = getattr(generator, "ledger", None)
    return Executor(
        name=f"assistant-service-{service.mode}",
        simulated=service.mode == "simulated",
        provider=describe.get("provider"),
        model=describe.get("model"),
        config_version=service.agent.version,
        respond=respond,
        configuration={
            "generator": describe,
            "agent": asdict(service.agent.config),
            "policy_context": service.agent.policy.describe(),
            "sdk": _sdk_version(describe.get("provider")),  # not part of the config hash
        },
        ledger_report=ledger.run_report if ledger is not None else None,
        trace=trace,
        execution=EXECUTION[service.mode],
        runtime_report=getattr(generator, "runtime", None),
        end_scenario=end_scenario,
        fixtures=bool(fixtures),
    )


def _sdk_version(provider: str | None) -> dict | None:
    if provider not in ("anthropic", "openai"):
        return None
    try:
        return {"package": provider, "version": version(provider)}
    except PackageNotFoundError:
        return {"package": provider, "version": None}


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
    suite: str = "development",
    scenarios_bytes: bytes | None = None,
) -> Path:
    """`scenarios_bytes`: the exact content already read (and, for validation, hash-checked)
    by the caller; the file is not read again, so another content cannot run in its place."""
    raw = scenarios_bytes if scenarios_bytes is not None else scenarios_path.read_bytes()
    data = json.loads(raw)
    errors = (validate_validation if suite == "validation" else validate_structure)(data)
    if errors:
        raise ValueError(f"scenario file is invalid for the {suite} suite ({len(errors)} errors)")
    chosen = select(data, ids, smoke)
    started_at = datetime.now(UTC)
    run_id = run_id or new_run_id(executor.simulated)
    runtime_before = _runtime(executor)
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
        environment = executor.end_scenario(scenario["id"]) if executor.end_scenario else None
        if environment is not None:
            record["environment"] = environment
        if record["turns"] or not stopped:
            records.append(record)
        if stopped:
            break

    not_run = [s["id"] for s in chosen if s["id"] not in {r["id"] for r in records}]
    report = {
        "report_format": REPORT_FORMAT,
        "run_id": run_id,
        "suite": suite,
        "simulated": executor.simulated,
        "execution": executor.execution,
        "trace_enabled": executor.trace,
        "notice": NOTICES[executor.execution],
        "graded": False,
        "executor": executor.name,
        "provider": executor.provider,
        "model": executor.model,
        "config_version": executor.config_version,
        "configuration": executor.configuration,
        "git": _git_state(),
        "scenarios_sha256": hashlib.sha256(raw).hexdigest(),  # the content that ran
        "scenario_contract_version": data["contract_version"],
        "data_version": data["data_version"],  # scenarios were written against this data
        "data_generations": sorted(
            {t["data_generation"] for r in records for t in r["turns"] if t.get("data_generation")}
        ),  # what the turns actually used
        "selection": "smoke" if smoke else (ids or "all"),
        "order": [s["id"] for s in chosen],  # scenario-file order unless --ids sets it
        "fixtures_enabled": executor.fixtures,
        "environment_groups": {
            "standard": [s["id"] for s in chosen if s["id"] not in _perturbed()],
            "perturbed": [s["id"] for s in chosen if s["id"] in _perturbed()],
        },
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
    if executor.execution == "local_inference":
        # No ledger operation exists for local inference: nothing paid, nothing fictitious.
        report["cost"] = {"api_usd": 0, "operational": "not measured"}
        report["runtime"] = {"before": runtime_before, "after": _runtime(executor)}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _perturbed() -> set[str]:
    """Scenarios whose tool results are changed by a fixture (reported apart)."""
    return {sid for sid, factory in FIXTURES.items() if _kind(factory) in PERTURBING_KINDS}


def _runtime(executor: Executor) -> dict | None:
    if executor.runtime_report is None:
        return None
    try:
        return executor.runtime_report()
    except Exception as error:  # recorded, never invented
        return {"error": getattr(error, "code", type(error).__name__)}


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
    parser.add_argument("--scenarios", type=Path, default=None, help="default: development set")
    parser.add_argument("--out-dir", type=Path, default=None, help="default: evals/results")
    parser.add_argument(
        "--suite",
        choices=["development", "validation"],
        default="development",
        help="validation: the owner's 10 reserved cases, validation budget, frozen config "
        "(needs --confirm-validation-run, --freeze-manifest, --scenarios and --out-dir "
        "outside the repository or in a git-ignored private folder)",
    )
    parser.add_argument("--freeze-manifest", type=Path, default=None)
    parser.add_argument("--confirm-validation-run", action="store_true")
    parser.add_argument(
        "--executor",
        choices=["placeholder", "simulated", "live", "local"],
        default="placeholder",
        help="placeholder: no service; simulated: full service with the simulated provider; "
        "live: real paid API calls (needs --confirm-paid-run and --batch-cap-usd); "
        "local: real inference on the local Ollama model (no API cost)",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        default="anthropic",
        help="paid provider for --executor live",
    )
    parser.add_argument("--num-ctx", type=int, default=None, help="local: Ollama context")
    parser.add_argument("--num-predict", type=int, default=None, help="local: output tokens")
    parser.add_argument(
        "--policy-context",
        choices=["full", "bm25", "embeddings"],
        default="full",
        help="manual sections in the prompt: full (baseline), BM25 top-k or embeddings top-k "
        "(EMP-12; embeddings: live only, prebuilt index, paid queries on the same budget)",
    )
    parser.add_argument(
        "--prompt-cache",
        choices=["off", "system-5m"],
        default="off",
        help="anthropic + full manual only: off (baseline) or one explicit 5-minute cache "
        "breakpoint at the end of the system prompt (caches tools + system)",
    )
    parser.add_argument("--confirm-paid-run", action="store_true")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="record each turn's model/tool steps (validated arguments, public tool results)",
    )
    parser.add_argument("--batch-cap-usd", type=Decimal, default=None)
    parser.add_argument(
        "--max-model-calls", type=int, default=AgentConfig.max_model_calls, help="per turn (1-5)"
    )
    parser.add_argument(
        "--max-run-seconds",
        type=float,
        default=900.0,
        help="no new turn starts after this; a running turn stops at the agent's turn limit",
    )
    args = parser.parse_args(argv)
    ids = args.ids.split(",") if args.ids else None
    try:
        if args.suite == "validation":
            _check_validation_args(args)
        args.scenarios = args.scenarios or DEFAULT_SCENARIOS
        args.out_dir = args.out_dir or DEFAULT_OUT
        args.scenarios_bytes = args.scenarios.read_bytes()  # read once: checked and run as is
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
            suite=args.suite,
            scenarios_bytes=args.scenarios_bytes,
        )
    except (OSError, ValueError) as error:
        print(f"FAILED: {error}")
        return 1
    label = {
        "simulated": "SIMULATED run",
        "local_inference": "LOCAL run (real inference, no API cost)",
        "paid_api": "LIVE run (real paid model calls)",
    }[executor.execution]
    print(f"{label} written to {path} (nothing graded)")
    return 0


def _check_validation_args(args) -> None:
    """Refused before any client, ledger write or call. The cases themselves stay owner-held."""
    from .freeze import private_path

    if not args.confirm_validation_run:
        raise ValueError("a validation run uses the reserved cases: add --confirm-validation-run")
    if args.executor != "live" or args.provider != "anthropic":
        raise ValueError("validation runs only the frozen candidate: --executor live, anthropic")
    if args.smoke or args.ids:
        raise ValueError("validation runs all reserved cases once: no --smoke or --ids")
    for name in ("scenarios", "out_dir", "freeze_manifest"):
        path = getattr(args, name)
        if path is None:
            raise ValueError(f"--{name.replace('_', '-')} is required for validation")
        if not private_path(path):
            raise ValueError(
                f"--{name.replace('_', '-')} must be outside the repository or in a "
                "git-ignored private folder"
            )


def _service_executor(args) -> tuple[Executor, str]:
    from dataclasses import replace as with_changes

    from .assistant import Settings, build_service, make_generator
    from .generation import AdapterConfigError, ProviderError

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
    local = args.executor == "local"
    if (args.num_ctx or args.num_predict) and not local:
        raise ValueError("--num-ctx and --num-predict apply to --executor local only")
    mode = "live" if live else "local" if local else "simulated"
    provider = {"live": args.provider, "local": "ollama", "simulated": "simulated"}[mode]
    if args.prompt_cache != "off" and (provider != "anthropic" or args.policy_context != "full"):
        raise ValueError("--prompt-cache needs --executor live --provider anthropic, full manual")
    settings = with_changes(
        settings,
        mode=mode,
        provider=provider,
        prompt_cache=args.prompt_cache,
        policy_context=args.policy_context,
    )
    overrides = {
        k: v for k, v in (("num_ctx", args.num_ctx), ("num_predict", args.num_predict)) if v
    }
    config = AgentConfig(max_model_calls=args.max_model_calls)
    validation = args.suite == "validation"
    freeze_check = _freeze_check(args) if validation else None  # before any client exists
    try:
        generator = make_generator(settings, **overrides)
        if local:
            generator.check_model()  # missing model or another build: refuse, never substitute
        service = build_service(
            settings,
            generator=generator,
            config=config,
            run_cap_micro_usd=cap_micro,  # the same make_policy(settings) the API uses
            budget="validation" if validation else "development",
        )
    except (AdapterConfigError, ProviderError) as error:
        raise ValueError(str(error)) from None
    run_id = new_run_id(mode == "simulated")
    if not validation:
        return service_executor(service, run_id, trace=args.trace), run_id
    if service.agent.version != freeze_check["config_version"]:
        raise ValueError("the built service does not match the frozen configuration")
    executor = service_executor(service, run_id + "-validation", trace=args.trace, fixtures={})
    executor.configuration.update(budget=service.budget, freeze_check=freeze_check)
    return executor, run_id + "-validation"


def _freeze_check(args) -> dict:
    import sqlite3

    from .freeze import current_state, divergences
    from .generations import GenerationError

    manifest_bytes = args.freeze_manifest.read_bytes()  # hashed and parsed from one read
    manifest = json.loads(manifest_bytes)
    try:
        current = current_state(
            prompt_cache=args.prompt_cache,
            policy_context=args.policy_context,
            max_model_calls=args.max_model_calls,
            scenarios=args.scenarios,
            scenarios_bytes=args.scenarios_bytes,  # the same bytes run() executes
        )
    except (ImportError, sqlite3.Error, GenerationError) as error:
        raise ValueError(f"cannot compute the frozen configuration: {error}") from None
    diffs = divergences(manifest, current)
    if current["git"].get("dirty") is not False:
        diffs.append("git.dirty: the working tree differs from the frozen commit")
    if diffs:
        raise ValueError("frozen configuration differs: " + "; ".join(diffs[:10]))
    return {
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "config_version": current["config_version"],
        "commit": current["git"].get("commit"),
        "divergences": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
