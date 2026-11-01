"""Budget ledger and guarded call path (EMP-7). All prices here are FICTITIOUS test values."""

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from emporio_assistant.budget import (
    MICRO,
    BudgetError,
    CallRequest,
    CallResult,
    InsufficientBudget,
    Ledger,
    LedgerConflict,
    LedgerHold,
    LedgerUnavailable,
    NotSent,
    UncertainUsage,
    guarded_call,
    reservation_micro_usd,
)
from emporio_assistant.pricing import DEFAULT_PATH, load_price_table

FAKE_PRICES = {
    "pricing_version": "test-fictitious",
    "fictitious": True,
    "models": [
        {
            "provider": "fake",
            "model": "fake-model",
            "unit": "usd_per_million_tokens",
            "source": "FICTITIOUS test value",
            "checked_on": "2026-11-01",
            "categories": {
                "input": {"usd_per_million": "1.00"},
                "output": {"usd_per_million": "5.00"},
                "reasoning": {"included_in": "output"},
            },
        }
    ],
}
LIMITS = {"input": 1000, "output": 200}  # reservation: 1000*1 + 200*5 = 2000 µUSD


@pytest.fixture
def pricing(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(FAKE_PRICES), encoding="utf-8")
    return load_price_table(path)


@pytest.fixture
def ledger(tmp_path):
    return Ledger.create(tmp_path / "budget" / "ledger.sqlite", "simulated")


def request(op_id="op-1", **overrides):
    fields = dict(
        op_id=op_id, budget="development", provider="fake", model="fake-model",
        limits=LIMITS, run_id="run-test", scenario_id="DEV-001",
    )  # fmt: skip
    return CallRequest(**{**fields, **overrides})


def ok(usage):
    return lambda _request: CallResult("simulated text", usage)


def available(ledger, budget="development"):
    return ledger.summary()["budgets"][budget]["available_micro_usd"]


def test_insufficient_budget_refuses_before_dispatch(ledger, pricing):
    called = []
    big = request(limits={"input": 8 * MICRO, "output": 1})  # 8 USD > 7 USD development cap
    with pytest.raises(InsufficientBudget):
        guarded_call(ledger, pricing, big, lambda r: called.append(r))
    assert called == []
    assert ledger.summary()["operations_by_state"] == {}


def test_reservation_reduces_available_balance(ledger):
    ledger.reserve(request(), 2000, "test")
    assert available(ledger) == 7 * MICRO - 2000
    assert ledger.summary()["budgets"]["development"]["held_micro_usd"] == 2000


def test_known_usage_is_settled_at_its_cost(ledger, pricing):
    usage = {"input": 500, "output": 100, "reasoning": 80}  # reasoning already counted in output
    guarded_call(ledger, pricing, request(), ok(usage))
    budget = ledger.summary()["budgets"]["development"]
    assert budget["confirmed_micro_usd"] == 500 + 500
    assert budget["held_micro_usd"] == 0


def test_repeated_reconciliation_does_not_charge_twice(ledger, pricing):
    usage = {"input": 500, "output": 100}
    guarded_call(ledger, pricing, request(), ok(usage))
    assert ledger.settle("op-1", usage, pricing) == 1000
    assert ledger.summary()["budgets"]["development"]["confirmed_micro_usd"] == 1000
    with pytest.raises(LedgerConflict):
        ledger.settle("op-1", {"input": 1, "output": 1}, pricing)


@pytest.mark.parametrize("failure", [TimeoutError("read timeout"), ConnectionError("reset")])
def test_failure_after_dispatch_keeps_the_reservation(ledger, pricing, failure):
    def boom(_request):
        raise failure

    with pytest.raises(UncertainUsage):
        guarded_call(ledger, pricing, request(), boom)
    summary = ledger.summary()
    assert summary["operations_by_state"] == {"uncertain": 1}
    assert available(ledger) == 7 * MICRO - 2000
    # Later reconciliation from known usage replaces the hold with the confirmed cost.
    assert ledger.settle("op-1", {"input": 10, "output": 0}, pricing) == 10
    assert available(ledger) == 7 * MICRO - 10


def test_missing_usage_is_uncertain(ledger, pricing):
    with pytest.raises(UncertainUsage):
        guarded_call(ledger, pricing, request(), ok(None))
    assert available(ledger) == 7 * MICRO - 2000


def test_proven_not_sent_releases_the_reservation(ledger, pricing):
    def refuse(_request):
        raise NotSent()

    with pytest.raises(BudgetError, match="not sent"):
        guarded_call(ledger, pricing, request(), refuse)
    assert ledger.summary()["operations_by_state"] == {"released": 1}
    assert available(ledger) == 7 * MICRO


def test_restart_preserves_state_and_missing_ledger_is_not_zero(ledger, pricing, tmp_path):
    guarded_call(ledger, pricing, request(), ok({"input": 500, "output": 100}))
    ledger.reserve(request("op-2"), 2000, "test")
    reopened = Ledger(ledger.path)
    assert reopened.summary() == ledger.summary()
    assert available(reopened) == 7 * MICRO - 1000 - 2000
    with pytest.raises(LedgerUnavailable):
        Ledger(tmp_path / "elsewhere" / "ledger.sqlite")
    assert not (tmp_path / "elsewhere" / "ledger.sqlite").exists()
    with pytest.raises(LedgerConflict):
        Ledger.create(ledger.path, "simulated")


def _reserve_in_process(path: str, index: int) -> str:
    try:
        Ledger(Path(path)).reserve(request(f"op-{index}"), 2 * MICRO, "test")
        return "reserved"
    except InsufficientBudget:
        return "refused"


def test_concurrent_processes_cannot_overspend(ledger):
    with ProcessPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(_reserve_in_process, [str(ledger.path)] * 8, range(8)))
    assert outcomes.count("reserved") == 3  # 3 x 2 USD fit in 7 USD; a 4th would not
    assert ledger.summary()["budgets"]["development"]["held_micro_usd"] == 6 * MICRO


def test_retries_must_be_accounted_as_new_attempts(ledger, pricing):
    with pytest.raises(BudgetError, match="SDK retries"):
        guarded_call(ledger, pricing, request(sdk_retries=2), ok({"input": 1, "output": 1}))
    guarded_call(ledger, pricing, request(), ok({"input": 1, "output": 1}))
    with pytest.raises(LedgerConflict):  # same operation id cannot be dispatched twice
        guarded_call(ledger, pricing, request(), ok({"input": 1, "output": 1}))
    guarded_call(ledger, pricing, request("op-1-retry", attempt=2), ok({"input": 1, "output": 1}))
    assert ledger.summary()["operations_by_state"] == {"settled": 2}


def test_unknown_price_or_missing_limits_are_refused(ledger, pricing):
    with pytest.raises(BudgetError, match="no verified price"):
        guarded_call(ledger, pricing, request(model="other"), ok({}))
    with pytest.raises(BudgetError, match="missing limits"):
        guarded_call(ledger, pricing, request(limits={"output": 10}), ok({}))
    with pytest.raises(BudgetError, match="output token limit"):
        guarded_call(ledger, pricing, request(limits={"input": 10, "output": 0}), ok({}))
    shipped = load_price_table(DEFAULT_PATH)  # versioned config: only verified entries
    assert not shipped.fictitious
    assert set(shipped.models) == {("anthropic", "claude-haiku-5-5"), ("openai", "gpt-6-luna")}
    with pytest.raises(BudgetError, match="no verified price"):
        reservation_micro_usd(request(provider="anthropic", model="claude-haiku-4-5"), shipped)


def test_margin_and_other_budget_are_never_used_automatically(ledger):
    ledger.reserve(request("dev-1"), 6_500_000, "test")
    with pytest.raises(InsufficientBudget):  # 7.5 USD total would still be under 10 USD
        ledger.reserve(request("dev-2"), 1 * MICRO, "test")
    with pytest.raises(InsufficientBudget):
        ledger.reserve(request("val-1", budget="validation"), 2_500_000, "test")
    ledger.reserve(request("val-2", budget="validation"), 2 * MICRO, "test")
    summary = ledger.summary()
    assert summary["margin_micro_usd"] == 1 * MICRO
    assert sum(b["available_micro_usd"] for b in summary["budgets"].values()) == 500_000


def test_overrun_is_recorded_and_blocks_new_calls(ledger, pricing):
    guarded_call(ledger, pricing, request(), ok({"input": 5000, "output": 200}))  # 6000 > 2000
    summary = ledger.summary()
    assert summary["budgets"]["development"]["confirmed_micro_usd"] == 6000
    assert [h["op_id"] for h in summary["open_holds"]] == ["op-1"]
    with pytest.raises(LedgerHold):
        guarded_call(ledger, pricing, request("op-2"), ok({"input": 1, "output": 1}))
    ledger.clear_hold("op-1", "reviewed")
    guarded_call(ledger, pricing, request("op-2"), ok({"input": 1, "output": 1}))


def test_unpriceable_usage_keeps_reservation_and_holds(ledger, pricing):
    with pytest.raises(UncertainUsage):
        guarded_call(ledger, pricing, request(), ok({"input": 1, "output": 1, "audio": 3}))
    summary = ledger.summary()
    assert summary["operations_by_state"] == {"uncertain": 1}
    assert len(summary["open_holds"]) == 1
    assert available(ledger) == 7 * MICRO - 2000


def test_live_ledger_refuses_fictitious_prices(tmp_path, pricing):
    live = Ledger.create(tmp_path / "live.sqlite", "live")
    with pytest.raises(BudgetError, match="fictitious"):
        guarded_call(live, pricing, request(), ok({"input": 1, "output": 1}))


@pytest.mark.parametrize("usage", [{}, {"input": 500}, {"output": 100}])
def test_empty_or_incomplete_usage_keeps_the_reservation(ledger, pricing, usage):
    with pytest.raises(UncertainUsage):
        guarded_call(ledger, pricing, request(), ok(usage))
    summary = ledger.summary()
    assert summary["operations_by_state"] == {"uncertain": 1}
    assert summary["budgets"]["development"]["confirmed_micro_usd"] == 0
    assert available(ledger) == 7 * MICRO - 2000  # nothing released
    assert [h["op_id"] for h in summary["open_holds"]] == ["op-1"]


def test_ledger_itself_refuses_empty_usage_whatever_the_adapter_does(ledger, pricing):
    ledger.reserve(request(), 2000, "test")
    ledger.mark_dispatched("op-1")
    with pytest.raises(UncertainUsage):
        ledger.settle("op-1", {}, pricing)
    assert available(ledger) == 7 * MICRO - 2000


def test_explicit_zero_usage_is_a_valid_report(ledger, pricing):
    guarded_call(ledger, pricing, request(), ok({"input": 0, "output": 0}))
    assert ledger.summary()["operations_by_state"] == {"settled": 1}
    assert available(ledger) == 7 * MICRO


def test_batch_cap_limits_one_run(ledger, pricing):
    capped = dict(run_id="pilot", run_cap_micro_usd=5000)
    guarded_call(ledger, pricing, request("a", **capped), ok({"input": 1000, "output": 200}))
    guarded_call(ledger, pricing, request("b", **capped), ok({"input": 1000, "output": 200}))
    with pytest.raises(InsufficientBudget, match="batch cap"):  # 4000 + 2000 > 5000
        guarded_call(ledger, pricing, request("c", **capped), ok({"input": 1, "output": 1}))
    guarded_call(ledger, pricing, request("d"), ok({"input": 1, "output": 1}))  # other run


def test_prompt_tier_is_enforced(tmp_path, ledger):
    tiered = json.loads(json.dumps(FAKE_PRICES))
    tiered["models"][0].update(max_prompt_tokens=1000, prompt_categories=["input"])
    path = tmp_path / "tiered.json"
    path.write_text(json.dumps(tiered), encoding="utf-8")
    pricing = load_price_table(path)
    with pytest.raises(BudgetError, match="priced tier"):
        guarded_call(ledger, pricing, request(limits={"input": 1001, "output": 1}), ok({}))
    with pytest.raises(UncertainUsage):  # reported prompt above the tier: cost unknown
        guarded_call(ledger, pricing, request(), ok({"input": 1001, "output": 1}))
    assert ledger.summary()["open_holds"]
