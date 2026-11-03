"""Embeddings policy context (EMP-12), OFFLINE: fake client, synthetic vectors, temporary
ledger. These tests check the implementation (ranking, index, cache, budget, citations), not
the semantic quality of the real embedding model."""

import math
import sqlite3
from types import SimpleNamespace as NS

import pytest
from conftest import prepare_generation
from test_policy_context import Answer

from emporio_assistant.agent import AgentConfig, config_version
from emporio_assistant.assistant import AssistantService, Settings, make_policy
from emporio_assistant.budget import UncertainUsage
from emporio_assistant.embeddings import DIMENSIONS, MODEL, EmbeddingError, OpenAIEmbedder
from emporio_assistant.generation import AdapterConfigError, CallContext
from emporio_assistant.policy_context import (
    BM25Policy,
    EmbeddingPolicy,
    FullManual,
    IndexUnavailable,
)
from emporio_assistant.pricing import PriceTable, PricingError
from emporio_assistant.tools import CatalogSnapshot

AXES = {"payment": 0, "pix": 0, "installment": 1, "return": 2, "synthetic": 3}


def vector_for(text: str) -> list[float]:
    """Synthetic: one axis per keyword found, plus a small constant so none is zero."""
    vector = [0.0] * DIMENSIONS
    for word, axis in AXES.items():
        if word in text.lower():
            vector[axis] += 1.0
    vector[-1] = 0.01
    return vector


class FakeEmbeddings:
    """Scripted `client.embeddings.create`; never opens a socket. `change` edits a response
    (or returns an exception to raise) to simulate invalid provider answers."""

    def __init__(self, change=None):
        self.embeddings, self.requests, self.change = self, [], change

    def create(self, **params):
        self.requests.append(params)
        texts = params["input"]
        tokens = sum(len(t.split()) for t in texts)
        response = NS(
            object="list",
            model=MODEL,
            data=[
                NS(object="embedding", index=i, embedding=vector_for(t))
                for i, t in enumerate(texts)
            ],  # fmt: skip
            usage=NS(prompt_tokens=tokens, total_tokens=tokens),
        )
        if self.change is not None:
            response = self.change(response)
        if isinstance(response, BaseException):
            raise response
        return response


def context(**changes) -> CallContext:
    return CallContext(run_id="emb-test", budget="development", **changes)


@pytest.fixture
def setup(generation_dir, live_ledger, pricing):
    client = FakeEmbeddings()
    policy = EmbeddingPolicy(OpenAIEmbedder(live_ledger, pricing, client=client), k=1)
    return policy, client, live_ledger


def built(policy, generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        return policy.build_index(snapshot, context(scenario_id="policy-index"))


def test_ranking_parent_expansion_and_input_only_accounting(setup, generation_dir):
    policy, client, ledger = setup
    build = built(policy, generation_dir)
    assert build["built"] and build["embedded"] == 4 and len(build["op_ids"]) == 1
    sent = client.requests[0]
    assert sent["model"] == MODEL and sent["dimensions"] == DIMENSIONS == 1536
    assert sent["encoding_format"] == "float" and 0 < sent["timeout"] <= 20
    with CatalogSnapshot(generation_dir) as snapshot:
        selection = policy.select(snapshot, [], "test installments?", context=context())
        assert selection.details["hits"][0]["section"] == "1.1"
        assert selection.section_ids == ("1", "1.1")  # parent added, manual order
        assert selection.details["expanded"] == [{"section": "1", "parent_of": "1.1"}]
        assert 0 < selection.details["hits"][0]["similarity"] <= 1
        wide = EmbeddingPolicy(policy.embedder, k=3).select(
            snapshot, [], "returns and installments", context=context()
        )
        scores = [h["similarity"] for h in wide.details["hits"]]
        assert scores == sorted(scores, reverse=True)
        assert {h["section"] for h in wide.details["hits"][:2]} == {"1.1", "2"}
    operations = ledger.run_report("emb-test")["operations"]
    assert len(operations) == 3 and {o["state"] for o in operations} == {"settled"}
    assert all(set(o["usage"]) == {"input"} for o in operations)  # no invented output
    assert all(o["model"] == MODEL for o in operations)
    assert ledger.summary()["budgets"]["validation"]["confirmed_micro_usd"] == 0


def test_query_is_previous_customer_messages_plus_current_only(setup, generation_dir):
    policy, client, _ = setup
    built(policy, generation_dir)
    history = [
        {"role": "user", "content": "primeira"},
        {"role": "assistant", "content": "return return (assistant text is never used)"},
        {"role": "user", "content": "segunda"},
        {"role": "assistant", "content": "x"},
        {"role": "user", "content": "terceira"},
        {"role": "assistant", "content": "y"},
    ]
    with CatalogSnapshot(generation_dir) as snapshot:
        selection = policy.select(snapshot, history, "atual", context=context())
    assert client.requests[-1]["input"] == ["segunda\nterceira\natual"]
    assert selection.details["history_user_turns_used"] == 2


def test_query_cache_index_reuse_and_reuse_across_generations(
    setup, generation_dir, csv_dir, manual
):
    policy, client, _ = setup
    built(policy, generation_dir)
    with CatalogSnapshot(generation_dir) as snapshot:
        first = policy.select(snapshot, [], "pix payment", context=context())
        again = policy.select(snapshot, [], "pix payment", context=context())
    assert len(first.op_ids) == 1 and again.op_ids == () and again.details["query_cache_hit"]
    assert again.section_ids == first.section_ids and policy.cache_hits == 1
    requests = len(client.requests)
    assert built(policy, generation_dir)["built"] is False  # same generation: reused file
    prepare_generation(csv_dir, manual, generation_dir)  # new generation, same manual text
    rebuilt = built(policy, generation_dir)
    assert rebuilt["built"] and rebuilt["embedded"] == 0 and rebuilt["reused"] == 4
    assert len(client.requests) == requests  # nothing paid again


def test_missing_or_incompatible_index_is_refused_before_any_query(setup, generation_dir):
    policy, client, _ = setup
    with CatalogSnapshot(generation_dir) as snapshot:
        with pytest.raises(IndexUnavailable):
            policy.select(snapshot, [], "pix", context=context())
        assert client.requests == []  # nothing paid, no silent fallback to the full manual
        policy.build_index(snapshot, context())
        path = policy.index_path(snapshot)
        for key, value in (("model", "text-embedding-3-large"), ("dimensions", "512")):
            with sqlite3.connect(path) as db:
                original = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
                db.execute("UPDATE meta SET value = ? WHERE key = ?", (value, key))
            fresh = EmbeddingPolicy(policy.embedder)
            with pytest.raises(IndexUnavailable):
                fresh.select(snapshot, [], "pix", context=context())
            with sqlite3.connect(path) as db:
                db.execute("UPDATE meta SET value = ? WHERE key = ?", (original[0], key))
        with sqlite3.connect(path) as db:  # a section's content no longer matches
            db.execute("UPDATE vectors SET content_sha256 = 'x' WHERE section_id = '2'")
        with pytest.raises(IndexUnavailable):
            EmbeddingPolicy(policy.embedder).select(snapshot, [], "pix", context=context())


def wrong_dimension(r):
    r.data[0].embedding = r.data[0].embedding[:-1]
    return r


def not_finite(r):
    r.data[0].embedding[5] = math.nan
    return r


def other_model(r):
    r.model = "text-embedding-3-large"
    return r


def fewer(r):
    r.data = []
    return r


@pytest.mark.parametrize("change", [wrong_dimension, not_finite, other_model, fewer])
def test_invalid_vectors_are_refused_after_their_cost_is_settled(
    generation_dir, live_ledger, pricing, change
):
    embedder = OpenAIEmbedder(live_ledger, pricing, client=FakeEmbeddings(change))
    with pytest.raises(EmbeddingError):
        embedder.embed(["pix"], context())
    (operation,) = live_ledger.run_report("emb-test")["operations"]
    assert operation["state"] == "settled"  # the provider answered: its usage is the cost


def no_usage(r):
    r.usage = None
    return r


def extra_tokens(r):
    r.usage.total_tokens += 3  # tokens beyond the input: the cost is not known
    return r


def timeout(_r):
    return TimeoutError("simulated timeout after dispatch")


@pytest.mark.parametrize("change", [no_usage, extra_tokens, timeout])
def test_unknown_outcome_keeps_the_reservation(live_ledger, pricing, change):
    embedder = OpenAIEmbedder(live_ledger, pricing, client=FakeEmbeddings(change))
    with pytest.raises(UncertainUsage):
        embedder.embed(["pix"], context())
    (operation,) = live_ledger.run_report("emb-test")["operations"]
    assert operation["state"] == "uncertain" and operation["reserved_micro_usd"] > 0
    assert operation["confirmed_micro_usd"] is None


def test_reservation_is_input_only_and_an_unknown_price_refuses_before_any_client(
    live_ledger, pricing
):
    embedder = OpenAIEmbedder(live_ledger, pricing, client=FakeEmbeddings())
    embedder.embed(["um texto de teste"], context(run_cap_micro_usd=10_000))
    (operation,) = live_ledger.run_report("emb-test")["operations"]
    assert operation["usage"] == {"input": 4}
    assert operation["reserved_micro_usd"] >= operation["confirmed_micro_usd"] > 0
    no_price = PriceTable(pricing.version, False, {})
    with pytest.raises(PricingError):
        OpenAIEmbedder(live_ledger, no_price, client=None)  # never reaches _new_client


def test_agent_gets_only_selected_sections_and_their_citations(setup, generation_dir):
    policy, _, _ = setup
    built(policy, generation_dir)
    generator = Answer("Sete dias.\n[fontes: 2, 1.1]")
    service = AssistantService(generator, generation_dir, policy=policy)
    diagnostics = {}
    reply = service.send(service.start_session().id, "test returns", "r1", None, diagnostics)
    system = generator.systems[-1]
    assert 'context="embeddings excerpt" sections="1 of' in system
    assert '<section id="2"' in system and '<section id="1.1"' not in system
    assert [s["section"] for s in reply["reply"]["sources"]] == ["2"]
    assert diagnostics["dropped_sections"] == ["1.1"]
    assert len(diagnostics["op_ids"]) == 1  # the query operation is part of the turn
    assert diagnostics["policy_context"]["mode"] == "embeddings"
    assert "similarity" not in str(reply)  # never shown to the customer


def test_retrieval_eval_refuses_paid_runs_without_confirmation_or_cap(capsys, monkeypatch):
    from emporio_assistant import openai_adapter, retrieval_eval

    def refuse(_timeout):
        raise AssertionError("a client was created")

    monkeypatch.setattr(openai_adapter, "_new_client", refuse)
    base = ["--policy", "embeddings"]
    for extra in ([], ["--confirm-paid-run"], ["--confirm-paid-run", "--batch-cap-usd", "0.06"],
                  ["--confirm-paid-run", "--batch-cap-usd", "0.02", "--method",
                   "scenario-aggregate"]):  # fmt: skip
        assert retrieval_eval.main([*base, *extra]) == 1
    output = capsys.readouterr().out
    assert "--confirm-paid-run" in output and "<= 0.05" in output


def test_index_and_query_costs_are_reported_apart(setup, generation_dir):
    from emporio_assistant.retrieval_eval import _paid_costs

    policy, _, ledger = setup
    build = built(policy, generation_dir)
    with CatalogSnapshot(generation_dir) as snapshot:
        for question in ("pix", "returns", "pix"):  # the repeated one is a cache hit
            policy.select(snapshot, [], question, context=context())
    costs = _paid_costs(ledger, "emb-test", build["op_ids"])
    assert costs["index"]["operations"] == 1 and costs["queries"]["operations"] == 2
    assert costs["index"]["input_tokens"] > costs["queries"]["input_tokens"] > 0
    assert (policy.cache_hits, policy.cache_misses) == (1, 2)


def test_freeze_pins_the_embeddings_index_file(setup, generation_dir, monkeypatch):
    from emporio_assistant import freeze, runner

    monkeypatch.setattr(runner, "_git_state", lambda: {"commit": "f" * 40, "dirty": False})
    policy, _, _ = setup

    def state():
        return freeze.current_state(prompt_cache="off", policy_context="embeddings",
                                    max_model_calls=4, scenarios=None,
                                    data_dir=generation_dir)  # fmt: skip

    assert state()["data"]["policy_index_sha256"] is None  # not built: freeze write refuses
    built(policy, generation_dir)
    frozen = state()
    assert frozen["data"]["policy_index_sha256"] and "openai" in frozen["versions"]
    with CatalogSnapshot(generation_dir) as snapshot:
        path = policy.index_path(snapshot)
    with sqlite3.connect(path) as db:  # e.g. rebuilt with other vectors
        db.execute("UPDATE meta SET value = '1' WHERE key = 'build_ms'")
    assert any("policy_index_sha256" in d for d in freeze.divergences(frozen, state()))
    with sqlite3.connect(path) as db:  # stale file under the expected name: not pinned
        db.execute("UPDATE vectors SET content_sha256 = 'x' WHERE rowid = 1")
    assert state()["data"]["policy_index_sha256"] is None


def test_make_policy_turns_an_unknown_embedding_price_into_a_config_error(tmp_path, monkeypatch):
    from emporio_assistant import assistant
    from emporio_assistant.pricing import PricingError

    class NoPrice:
        def get(self, provider, model):
            raise PricingError("no price")

    monkeypatch.setattr(assistant, "Ledger", lambda path: object())
    monkeypatch.setattr(assistant, "load_price_table", lambda path: NoPrice())
    settings = Settings("live", tmp_path, tmp_path, tmp_path, (), False,
                        policy_context="embeddings")  # fmt: skip
    with pytest.raises(AdapterConfigError):
        make_policy(settings)


def test_identity_is_distinct_and_existing_hashes_stay(tmp_path):
    generator = Answer("x")
    full = config_version(generator, AgentConfig(), FullManual())
    bm25 = config_version(generator, AgentConfig(), BM25Policy())
    embeddings = config_version(generator, AgentConfig(), EmbeddingPolicy())
    assert len({full, bm25, embeddings}) == 3
    assert full == config_version(generator, AgentConfig())  # baseline unchanged
    describe = EmbeddingPolicy().describe()
    assert (describe["model"], describe["dimensions"], describe["k"]) == (MODEL, 1536, 4)
    settings = Settings(None, tmp_path, tmp_path, tmp_path, (), False, policy_context="embeddings")
    for mode in ("simulated", "local", None):  # paid queries never outside live mode
        with pytest.raises(AdapterConfigError):
            make_policy(Settings(**{**settings.__dict__, "mode": mode}))
