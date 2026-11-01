"""API budget ledger and the single guarded path for paid calls (EMP-7).

Money is integer micro-USD. The ledger is a local SQLite file, separate from the catalog, that
survives restarts. Every write runs in a `BEGIN IMMEDIATE` transaction, which SQLite serializes
across processes, so two reservations can't spend the same balance. No transaction stays open
while a provider call runs.

Operation states:
  reserved   -> worst-case cost held, not sent yet
  dispatched -> handed to the provider; from here a failure does not prove it was free
  settled    -> confirmed cost recorded from reported usage (final)
  released   -> never sent; reservation returned
  uncertain  -> sent (or maybe sent) without known usage; the reservation stays held

This ledger only controls application paths that call `guarded_call`. It does not see or stop
spending made elsewhere on the provider accounts, nor Claude Code's own usage.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .pricing import PriceTable, PricingError

MICRO = 1_000_000
TOTAL_MICRO_USD = 10 * MICRO
CAPS_MICRO_USD = {"development": 7 * MICRO, "validation": 2 * MICRO}
MARGIN_MICRO_USD = 1 * MICRO  # never spendable by code; only Matheus may reassign it
assert sum(CAPS_MICRO_USD.values()) + MARGIN_MICRO_USD == TOTAL_MICRO_USD
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "budget" / "ledger.sqlite"
SCHEMA_VERSION = "1"
MODES = {"live", "simulated"}

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
CREATE TABLE operations (
    op_id TEXT PRIMARY KEY,
    budget TEXT NOT NULL CHECK (budget IN ('development', 'validation')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    pricing_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    data_version TEXT NOT NULL,
    run_id TEXT NOT NULL,
    scenario_id TEXT,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    state TEXT NOT NULL
        CHECK (state IN ('reserved', 'dispatched', 'settled', 'released', 'uncertain')),
    limits_json TEXT NOT NULL,
    reserved_micro_usd INTEGER NOT NULL CHECK (reserved_micro_usd >= 0),
    confirmed_micro_usd INTEGER CHECK (confirmed_micro_usd >= 0),
    usage_json TEXT,
    error_type TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE holds (
    op_id TEXT PRIMARY KEY REFERENCES operations(op_id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cleared_at TEXT,
    note TEXT
) STRICT;
"""

# What each state still commits against the budget.
COMMITTED_SQL = """
SELECT COALESCE(SUM(CASE
    WHEN state IN ('reserved', 'dispatched', 'uncertain') THEN reserved_micro_usd
    WHEN state = 'settled' THEN confirmed_micro_usd
    ELSE 0 END), 0)
FROM operations WHERE budget = ?
"""
COMMITTED_RUN_SQL = COMMITTED_SQL.replace("WHERE budget = ?", "WHERE run_id = ?")


class BudgetError(Exception):
    """The paid path refused or stopped. Messages carry no secrets."""


class LedgerUnavailable(BudgetError):
    pass


class InsufficientBudget(BudgetError):
    pass


class LedgerHold(BudgetError):
    """New calls are blocked until Matheus reconciles a recorded problem."""


class LedgerConflict(BudgetError):
    """Duplicate operation id or an invalid state change."""


class UncertainUsage(BudgetError):
    """The call may have run and its cost is unknown; the reservation stays held."""


class NotSent(Exception):
    """Raised by an adapter only when it knows the request never reached the provider."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CallRequest:
    op_id: str  # unique per attempt; reusing it is refused
    budget: str  # development | validation
    provider: str
    model: str
    limits: dict[str, int]  # max units per billable category (input, output, cache_write...)
    run_id: str
    attempt: int = 1
    scenario_id: str | None = None
    config_version: str = "unversioned"
    data_version: str = "unknown"
    sdk_retries: int = 0  # adapters must disable SDK retries; each retry is a new request
    timeout_s: float = 60.0  # the adapter must pass this to its SDK
    run_cap_micro_usd: int | None = None  # optional extra ceiling for one batch (run_id)


@dataclass(frozen=True)
class CallResult:
    value: Any
    usage: dict[str, int] | None  # None means the provider reported no usage


class Ledger:
    """Open an existing ledger. A missing file is an error, never a fresh zero balance."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        try:
            with closing(self._connect()) as connection:
                meta = dict(connection.execute("SELECT key, value FROM meta"))
        except sqlite3.Error:
            raise LedgerUnavailable("budget ledger is missing or unreadable") from None
        if meta.get("schema_version") != SCHEMA_VERSION or meta.get("mode") not in MODES:
            raise LedgerUnavailable("budget ledger has an unexpected format")
        self.mode = meta["mode"]

    @classmethod
    def create(cls, path: Path, mode: str) -> Ledger:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {sorted(MODES)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise LedgerConflict("a ledger already exists at this path")
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.executescript(
                "BEGIN;" + SCHEMA + "INSERT INTO meta VALUES ('schema_version', "
                f"'{SCHEMA_VERSION}'), ('mode', '{mode}'), ('created_at', '{_now()}');COMMIT;"
            )
        finally:
            connection.close()
        return cls(path)

    def _connect(self) -> sqlite3.Connection:
        uri = self.path.resolve().as_uri() + "?mode=rw"  # never creates a new empty file
        connection = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _write(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        try:
            connection = self._connect()
        except sqlite3.Error:
            raise LedgerUnavailable("budget ledger is unavailable") from None
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = fn(connection)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result
        except sqlite3.Error:
            raise LedgerUnavailable("budget ledger write failed") from None
        finally:
            connection.close()

    def _operation(self, connection: sqlite3.Connection, op_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM operations WHERE op_id = ?", (op_id,)).fetchone()
        if row is None:
            raise LedgerConflict(f"unknown operation {op_id}")
        return row

    def reserve(self, request: CallRequest, reserved: int, pricing_version: str) -> None:
        def run(connection: sqlite3.Connection) -> None:
            if connection.execute("SELECT 1 FROM holds WHERE cleared_at IS NULL").fetchone():
                raise LedgerHold("ledger has an open hold; reconcile before new calls")
            if connection.execute(
                "SELECT 1 FROM operations WHERE op_id = ?", (request.op_id,)
            ).fetchone():
                raise LedgerConflict(f"operation {request.op_id} already exists")
            committed = connection.execute(COMMITTED_SQL, (request.budget,)).fetchone()[0]
            if request.run_cap_micro_usd is not None:
                run_committed = connection.execute(COMMITTED_RUN_SQL, (request.run_id,)).fetchone()[
                    0
                ]
                if run_committed + reserved > request.run_cap_micro_usd:
                    raise InsufficientBudget(
                        f"run {request.run_id}: reserving {reserved} µUSD would exceed the "
                        f"batch cap ({run_committed} of {request.run_cap_micro_usd} committed)"
                    )
            if committed + reserved > CAPS_MICRO_USD[request.budget]:
                raise InsufficientBudget(
                    f"{request.budget}: reserving {reserved} µUSD would exceed the cap "
                    f"({committed} of {CAPS_MICRO_USD[request.budget]} committed)"
                )
            now = _now()
            connection.execute(
                "INSERT INTO operations VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, NULL, NULL, NULL, NULL, ?, ?)",
                (
                    request.op_id, request.budget, request.provider, request.model,
                    pricing_version, request.config_version, request.data_version,
                    request.run_id, request.scenario_id, request.attempt,
                    json.dumps(request.limits, sort_keys=True), reserved, now, now,
                ),
            )  # fmt: skip

        self._write(run)

    def _transition(self, op_id: str, allowed: set[str], **fields: Any) -> None:
        def run(connection: sqlite3.Connection) -> None:
            state = self._operation(connection, op_id)["state"]
            if state not in allowed:
                raise LedgerConflict(f"operation {op_id} is {state}, expected {sorted(allowed)}")
            columns = ", ".join(f"{name} = ?" for name in fields)
            connection.execute(
                f"UPDATE operations SET {columns}, updated_at = ? WHERE op_id = ?",
                (*fields.values(), _now(), op_id),
            )

        self._write(run)

    def mark_dispatched(self, op_id: str) -> None:
        self._transition(op_id, {"reserved"}, state="dispatched")

    def release_unsent(self, op_id: str, error_type: str, duration_ms: int | None = None) -> None:
        """Only for a request that provably never reached the provider (see NotSent)."""
        self._transition(
            op_id,
            {"reserved", "dispatched"},
            state="released",
            error_type=error_type,
            duration_ms=duration_ms,
        )

    def mark_uncertain(self, op_id: str, error_type: str, duration_ms: int | None = None) -> None:
        self._transition(
            op_id, {"dispatched"}, state="uncertain", error_type=error_type, duration_ms=duration_ms
        )

    def settle(
        self,
        op_id: str,
        usage: dict[str, int],
        pricing: PriceTable,
        duration_ms: int | None = None,
    ) -> int:
        """Record the confirmed cost of known usage. Idempotent for the same usage."""
        usage_json = json.dumps(usage, sort_keys=True)

        def run(connection: sqlite3.Connection) -> int:
            row = self._operation(connection, op_id)
            if row["state"] == "settled":
                if row["usage_json"] != usage_json:
                    raise LedgerConflict(f"operation {op_id} was settled with other usage")
                return row["confirmed_micro_usd"]
            if row["state"] not in {"dispatched", "uncertain"}:
                raise LedgerConflict(f"operation {op_id} is {row['state']}; cannot settle")
            try:
                price = pricing.get(row["provider"], row["model"])
                cost = price.settlement_cost_micro_usd(usage)
            except PricingError as error:
                self._hold(connection, op_id, f"usage cannot be priced: {error}")
                connection.execute(
                    "UPDATE operations SET state = 'uncertain', usage_json = ?, updated_at = ? "
                    "WHERE op_id = ?",
                    (usage_json, _now(), op_id),
                )
                return -1
            connection.execute(
                "UPDATE operations SET state = 'settled', confirmed_micro_usd = ?, usage_json = ?,"
                " duration_ms = COALESCE(?, duration_ms), updated_at = ? WHERE op_id = ?",
                (cost, usage_json, duration_ms, _now(), op_id),
            )
            if cost > row["reserved_micro_usd"]:
                self._hold(connection, op_id, "confirmed cost exceeded the reservation")
            return cost

        cost = self._write(run)
        if cost < 0:
            raise UncertainUsage(f"operation {op_id}: usage could not be priced; ledger on hold")
        return cost

    @staticmethod
    def _hold(connection: sqlite3.Connection, op_id: str, reason: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO holds (op_id, reason, created_at) VALUES (?, ?, ?)",
            (op_id, reason, _now()),
        )

    def clear_hold(self, op_id: str, note: str) -> None:
        """Owner action after reviewing the operation; the recorded cost is kept."""

        def run(connection: sqlite3.Connection) -> None:
            updated = connection.execute(
                "UPDATE holds SET cleared_at = ?, note = ? WHERE op_id = ? AND cleared_at IS NULL",
                (_now(), note, op_id),
            ).rowcount
            if not updated:
                raise LedgerConflict(f"no open hold for {op_id}")

        self._write(run)

    def summary(self) -> dict:
        """Totals and open problems; no secrets, prompts or responses are stored at all."""
        try:
            with closing(self._connect()) as connection:
                budgets = {}
                for budget, cap in CAPS_MICRO_USD.items():
                    committed = connection.execute(COMMITTED_SQL, (budget,)).fetchone()[0]
                    settled = connection.execute(
                        "SELECT COALESCE(SUM(confirmed_micro_usd), 0) FROM operations "
                        "WHERE budget = ? AND state = 'settled'",
                        (budget,),
                    ).fetchone()[0]
                    budgets[budget] = {
                        "cap_micro_usd": cap,
                        "confirmed_micro_usd": settled,
                        "held_micro_usd": committed - settled,
                        "available_micro_usd": max(cap - committed, 0),
                    }
                states = dict(
                    connection.execute("SELECT state, COUNT(*) FROM operations GROUP BY state")
                )
                holds = [
                    dict(r)
                    for r in connection.execute(
                        "SELECT op_id, reason, created_at FROM holds WHERE cleared_at IS NULL"
                    )
                ]
        except sqlite3.Error:
            raise LedgerUnavailable("budget ledger is unavailable") from None
        return {
            "mode": self.mode,
            "total_cap_micro_usd": TOTAL_MICRO_USD,
            "margin_micro_usd": MARGIN_MICRO_USD,
            "budgets": budgets,
            "operations_by_state": states,
            "open_holds": holds,
        }


def reservation_micro_usd(request: CallRequest, pricing: PriceTable) -> int:
    """Worst-case cost from the declared limits. Refuses missing or invalid limits."""
    if request.budget not in CAPS_MICRO_USD:
        raise BudgetError(f"unknown budget '{request.budget}'")
    if request.sdk_retries != 0:
        raise BudgetError("SDK retries must be disabled; account each retry as a new request")
    if type(request.attempt) is not int or request.attempt < 1:
        raise BudgetError("attempt must be a positive integer")
    try:
        price = pricing.get(request.provider, request.model)
        missing = set(price.prices) - set(request.limits)
        if missing:
            raise BudgetError(f"missing limits for billable categories {sorted(missing)}")
        if "output" in price.prices and request.limits.get("output", 0) <= 0:
            raise BudgetError("an output token limit is required")
        if price.max_prompt_tokens is not None and any(
            request.limits.get(c, 0) > price.max_prompt_tokens for c in price.prompt_categories
        ):
            raise BudgetError("prompt limit is above the priced tier; refusing")
        return price.cost_micro_usd(request.limits)
    except PricingError as error:
        raise BudgetError(f"refused: {error}") from None


def guarded_call(
    ledger: Ledger,
    pricing: PriceTable,
    request: CallRequest,
    call: Callable[[CallRequest], CallResult],
) -> CallResult:
    """Validate, reserve, run one attempt, then settle or keep the reservation as uncertain."""
    if ledger.mode == "live" and pricing.fictitious:
        raise BudgetError("fictitious prices cannot be used with a live ledger")
    reserved = reservation_micro_usd(request, pricing)
    ledger.reserve(request, reserved, pricing.version)

    started = time.monotonic()

    def elapsed() -> int:
        return round((time.monotonic() - started) * 1000)

    # If this write fails the call never starts and the reservation stays held (conservative).
    ledger.mark_dispatched(request.op_id)
    try:
        result = call(request)
    except NotSent:
        # Adapter contract: raised only when the request provably never left the process.
        ledger.release_unsent(request.op_id, "not_sent", elapsed())
        raise BudgetError(f"operation {request.op_id} was not sent; reservation released") from None
    except BaseException as error:
        # Anything else may have run at the provider: keep the reservation.
        ledger.mark_uncertain(request.op_id, type(error).__name__, elapsed())
        if not isinstance(error, Exception):
            raise
        raise UncertainUsage(
            f"operation {request.op_id} failed after dispatch ({type(error).__name__}); "
            "reservation kept"
        ) from error
    if result.usage is None:
        ledger.mark_uncertain(request.op_id, "missing_usage", elapsed())
        raise UncertainUsage(f"operation {request.op_id} returned no usage; reservation kept")
    ledger.settle(request.op_id, result.usage, pricing, elapsed())
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Inspect or initialize the API budget ledger.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a new empty ledger (refuses to overwrite)")
    init.add_argument("--mode", choices=sorted(MODES), required=True)
    commands.add_parser("summary", help="print totals and open holds as JSON")
    settle = commands.add_parser(
        "settle", help="owner action: record usage confirmed in the provider console"
    )
    settle.add_argument("op_id")
    settle.add_argument("--usage", required=True, help='JSON, e.g. {"input": 10, "output": 5}')
    settle.add_argument("--pricing", type=Path, default=None)
    clear = commands.add_parser("clear-hold", help="owner action after reviewing an operation")
    clear.add_argument("op_id")
    clear.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            Ledger.create(args.ledger, args.mode)
            print(f"OK: created {args.mode} ledger")
        elif args.command == "summary":
            print(json.dumps(Ledger(args.ledger).summary(), indent=2))
        elif args.command == "settle":
            from .pricing import DEFAULT_PATH as PRICING_PATH
            from .pricing import load_price_table

            pricing = load_price_table(args.pricing or PRICING_PATH)
            cost = Ledger(args.ledger).settle(args.op_id, json.loads(args.usage), pricing)
            print(f"OK: {args.op_id} settled at {cost} µUSD")
        else:
            Ledger(args.ledger).clear_hold(args.op_id, args.note)
            print(f"OK: hold on {args.op_id} cleared")
    except (BudgetError, PricingError, json.JSONDecodeError) as error:
        print(f"FAILED: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
