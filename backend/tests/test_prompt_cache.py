"""Opt-in 5-minute prompt cache on the system prompt, with a FAKE client (offline). These tests
check payload and accounting only; whether the provider reports a hit is a run measurement."""

import pytest
from conftest import USAGE_OK, FakeAnthropic, text

from emporio_assistant.agent import Agent, AgentConfig, build_system, config_version
from emporio_assistant.anthropic_adapter import (
    AdapterConfigError,
    AnthropicGenerator,
    CallContext,
    GenerationSettings,
)
from emporio_assistant.assistant import Settings, make_generator
from emporio_assistant.budget import UncertainUsage
from emporio_assistant.runner import main
from emporio_assistant.tools import CatalogSnapshot

CTX = CallContext(run_id="test-run")
CACHED = GenerationSettings(prompt_cache="system-5m")


@pytest.fixture
def snap(generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        yield snapshot


def run(live_ledger, pricing, snap, settings, *script):
    client = FakeAnthropic(*script)
    agent = Agent(AnthropicGenerator(live_ledger, pricing, settings, client=client), AgentConfig())
    for _ in script:
        agent.run_turn(snap, [], "Que horas abrem?", CTX)
    return agent, client


def ops(ledger):
    return ledger.run_report("test-run")["operations"]


def usage(input_tokens, write_5m, read):
    return {
        **USAGE_OK,
        "input_tokens": input_tokens,
        "output_tokens": 100,
        "cache_creation_input_tokens": write_5m,
        "cache_read_input_tokens": read,
        "cache_creation": {"ephemeral_5m_input_tokens": write_5m, "ephemeral_1h_input_tokens": 0},
    }


def test_off_keeps_the_baseline_payload_and_description(live_ledger, pricing, snap):
    agent, client = run(live_ledger, pricing, snap, GenerationSettings(), text("ok"))
    params = client.requests[0]
    assert params["system"] == build_system(snap)  # plain string, as before
    assert "cache_control" not in repr(params)
    assert "prompt_cache" not in agent.generator.describe()


def test_enabled_marks_only_the_end_of_the_same_system_text(live_ledger, pricing, snap):
    _, off = run(live_ledger, pricing, snap, GenerationSettings(), text("ok"))
    _, on = run(live_ledger, pricing, snap, CACHED, text("ok"))
    a, b = off.requests[0], on.requests[0]
    assert b["system"] == [
        {"type": "text", "text": a["system"], "cache_control": {"type": "ephemeral", "ttl": "5m"}}
    ]
    assert repr(b).count("cache_control") == 1  # no breakpoint in tools or messages
    assert {k: v for k, v in a.items() if k != "system"} == {
        k: v for k, v in b.items() if k != "system"
    }


def test_enabled_has_its_own_description_and_hash():
    class Probe:
        mode = "live"
        describe = AnthropicGenerator.describe

    off, on = Probe(), Probe()
    off.settings, on.settings = GenerationSettings(), CACHED
    config = AgentConfig(max_model_calls=4)
    assert config_version(off, config) == "30baf5c5f663"  # candidate prompt, cache off
    assert config_version(on, config) == "d9f674fcc08d"  # candidate prompt, system-5m
    assert on.describe()["prompt_cache"]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}


def test_invalid_cache_configuration_is_refused(live_ledger, pricing, generation_dir, tmp_path):
    for mode in ("system-1h", "auto", ""):
        with pytest.raises(AdapterConfigError, match="prompt_cache"):
            AnthropicGenerator(
                live_ledger, pricing, GenerationSettings(prompt_cache=mode), client=FakeAnthropic()
            )
    for mode, provider in (("live", "openai"), ("local", "ollama"), ("simulated", "simulated")):
        settings = Settings(mode, generation_dir, tmp_path / "l.sqlite", tmp_path, (), False)
        settings = Settings(**{**vars(settings), "provider": provider, "prompt_cache": "system-5m"})
        with pytest.raises(AdapterConfigError, match="anthropic provider only"):
            make_generator(settings)


def test_cli_refuses_cache_outside_anthropic_full_manual(tmp_path, capsys):
    base = ["--ids", "DEV-026", "--out-dir", str(tmp_path), "--prompt-cache", "system-5m"]
    paid = ["--executor", "live", "--confirm-paid-run", "--batch-cap-usd", "0.05"]
    assert main([*base, *paid, "--provider", "openai"]) == 1
    assert main([*base, *paid, "--policy-context", "bm25"]) == 1
    assert main([*base, "--executor", "simulated"]) == 1
    assert "--prompt-cache needs" in capsys.readouterr().out
    assert list(tmp_path.glob("*.json")) == []  # nothing ran


def test_cache_write_then_read_are_priced_once_each(live_ledger, pricing, snap):
    run(
        live_ledger,
        pricing,
        snap,
        CACHED,
        text("ok", usage=usage(50, 8000, 0)),
        text("ok", usage=usage(60, 0, 8000)),
    )
    write, read = ops(live_ledger)
    # USD per million: input 0.10, 5m write 0.125, read 0.01, output 0.50 -> µUSD
    assert write["confirmed_micro_usd"] == 5 + 1000 + 50
    assert read["confirmed_micro_usd"] == 6 + 80 + 50
    assert write["usage"] == {
        "input": 50,
        "output": 100,
        "cache_read": 0,
        "cache_write_5m": 8000,
        "cache_write_1h": 0,
    }
    assert read["usage"]["cache_read"] == 8000 and read["usage"]["cache_write_5m"] == 0


def test_reservation_never_assumes_a_hit(live_ledger, pricing, snap):
    run(live_ledger, pricing, snap, GenerationSettings(), text("ok"))
    run(live_ledger, pricing, snap, CACHED, text("ok", usage=usage(50, 0, 8000)))
    off, on = ops(live_ledger)
    assert on["reserved_micro_usd"] >= off["reserved_micro_usd"]


@pytest.mark.parametrize(
    "partial",
    [
        {"input_tokens": 50, "output_tokens": 100, "cache_read_input_tokens": 8000},
        {**usage(50, 8000, 0), "cache_creation": None},  # write with unknown duration
    ],
)
def test_incomplete_cache_usage_keeps_the_reservation(live_ledger, pricing, snap, partial):
    with pytest.raises(UncertainUsage):
        run(live_ledger, pricing, snap, CACHED, text("ok", usage=partial))
    summary = live_ledger.summary()
    assert summary["operations_by_state"] == {"uncertain": 1} and summary["open_holds"]
    assert summary["budgets"]["development"]["held_micro_usd"] > 0
