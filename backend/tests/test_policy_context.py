"""EMP-12 policy context: full-manual baseline unchanged, BM25 selection, index and metadata."""

import hashlib
import json

import pytest

from emporio_assistant.agent import (
    INSTRUCTIONS,
    AgentConfig,
    build_system,
    config_version,
    render_manual,
)
from emporio_assistant.assistant import AssistantService
from emporio_assistant.generation import END_TURN, ModelReply
from emporio_assistant.policy_context import BM25Policy, FullManual, query_terms
from emporio_assistant.retrieval_eval import evaluate
from emporio_assistant.tools import CatalogSnapshot


class Answer:
    mode = "simulated"

    def __init__(self, text):
        self.text, self.systems = text, []

    def describe(self):
        return {"provider": "test-answer"}

    def create(self, *, system, messages, tools, context):
        self.systems.append(system)
        return ModelReply(self.text, [], END_TURN)


def files_digest(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob("*")
            if p.is_file()}  # fmt: skip


def test_full_manual_is_the_unchanged_baseline(generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        selection = FullManual().select(snapshot, [], "qualquer coisa")
        assert build_system(snapshot, selection) == build_system(snapshot)
        assert build_system(snapshot) == INSTRUCTIONS + "\n\n" + render_manual(snapshot)
        assert selection.section_ids == tuple(s["id"] for s in snapshot.policies()["sections"])
    generator = Answer("x")
    full = config_version(generator, AgentConfig(), FullManual())
    assert full == config_version(generator, AgentConfig())  # no new field in the hash
    assert config_version(generator, AgentConfig(), BM25Policy()) != full


def test_index_is_derived_outside_the_generation_and_reused(generation_dir):
    generations = generation_dir / "generations"
    before = files_digest(generations)
    with CatalogSnapshot(generation_dir) as snapshot:
        path = BM25Policy().index_path(snapshot)
        assert path.parent == generation_dir / "indexes"
        assert snapshot.id in path.name and path.name.startswith("policies-bm25-")
        stamp = path.stat().st_mtime_ns
        assert BM25Policy().index_path(snapshot) == path  # valid metadata: not rebuilt
        assert path.stat().st_mtime_ns == stamp
        path.write_bytes(b"not an index")  # broken or stale file: rebuilt
        assert BM25Policy().index_path(snapshot) == path and path.read_bytes()[:6] == b"SQLite"
    assert files_digest(generations) == before  # catalog and generation untouched


def test_selection_ranks_sections_expands_parents_and_records_metadata(generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        policy = BM25Policy(k=1)
        returns = policy.select(snapshot, [], "How many test days for returns?")
        assert returns.section_ids == ("2",) and returns.details["hits"][0]["section"] == "2"
        assert returns.details["hits"][0]["score"] > 0 and returns.details["no_match"] is False
        installments = policy.select(snapshot, [], "test installments")
        assert installments.details["hits"][0]["section"] == "1.1"
        assert installments.details["expanded"] == [{"section": "1", "parent_of": "1.1"}]
        assert installments.section_ids == ("1", "1.1")  # manual order
        record = installments.record({s["id"]: s for s in snapshot.policies()["sections"]})
        assert record["mode"] == "bm25" and record["sections"] == ["1", "1.1"]
        assert record["index"].startswith("policies-bm25-") and record["k"] == 1


def test_history_uses_earlier_user_messages_only(generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        history = [
            {"role": "user", "content": "returns policy"},
            {"role": "assistant", "content": "installments installments"},
        ]
        selection = BM25Policy(k=1).select(snapshot, history, "how many days?")
        assert selection.section_ids == ("2",)
        assert selection.details["history_user_turns_used"] == 1
        alone = BM25Policy(k=1, history_user_turns=0).select(snapshot, history, "days?")
        assert alone.details["history_user_turns_used"] == 0


@pytest.mark.parametrize(
    "text, reason",
    [
        ("", "no query terms"),
        ("? ! ... ** de a o", "no query terms"),
        ('AND OR * " NEAR( zzqx: ^wwvy', "no section matched"),
        ("zzqx wwvy", "no section matched"),
    ],
)
def test_empty_or_special_queries_select_nothing_and_say_so(generation_dir, text, reason):
    with CatalogSnapshot(generation_dir) as snapshot:
        selection = BM25Policy().select(snapshot, [], text)
    assert selection.section_ids == () and selection.details["no_match"] is True
    assert selection.details["no_match_reason"] == reason


def test_query_terms_are_folded_and_operators_become_words():
    assert query_terms("Devolução do VIOLÃO, devolução!") == ["devolucao", "violao"]
    assert query_terms("NEAR AND") == ["near"]  # "and" is a function word; no FTS syntax


def test_agent_gives_only_selected_sections_and_accepts_only_their_citations(generation_dir):
    generator = Answer("Até sete dias.\n[fontes: 2, 1.1]")
    service = AssistantService(generator, generation_dir, policy=BM25Policy(k=1))
    session = service.start_session()
    diagnostics = {}
    reply = service.send(session.id, "test returns days", "r1", None, diagnostics)
    system = generator.systems[-1]
    assert 'context="bm25 excerpt" sections="1 of' in system
    assert '<section id="2"' in system and '<section id="1.1"' not in system
    assert reply["reply"]["sources"][0]["section"] == "2" and len(reply["reply"]["sources"]) == 1
    assert diagnostics["dropped_sections"] == ["1.1"]  # exists in the manual, not in context
    assert diagnostics["policy_context"]["sections"] == ["2"]
    assert "policy_context" not in reply  # diagnostics never enter the public reply


def test_no_match_gives_no_manual_section_and_no_silent_fallback(generation_dir):
    generator = Answer("Não encontrei essa regra.")
    service = AssistantService(generator, generation_dir, policy=BM25Policy())
    session = service.start_session()
    diagnostics = {}
    service.send(session.id, "zzqx wwvy", "r1", None, diagnostics)
    assert "(no manual section was retrieved for this message)" in generator.systems[-1]
    assert "<section " not in generator.systems[-1]
    assert diagnostics["policy_context"]["no_match"] is True


def test_full_manual_diagnostics_are_small(generation_dir):
    generator = Answer("Olá.")
    service = AssistantService(generator, generation_dir)
    diagnostics = {}
    service.send(service.start_session().id, "oi", "r1", None, diagnostics)
    assert set(diagnostics["policy_context"]) == {"mode", "section_count", "chars"}


def test_evaluation_scores_only_scenarios_with_manual_references_and_no_future_turns(
    generation_dir,
):
    scenarios = [
        {
            "id": "S-1",
            "category": "policies",
            "language": "en",
            "references": {"manual": [{"section": "2"}]},
            "turns": [{"user": "zzqx"}, {"user": "test returns"}],
        },
        {
            "id": "S-2",
            "category": "catalog",
            "language": "en",
            "references": {"manual": []},
            "turns": [{"user": "test returns"}],
        },
    ]
    with CatalogSnapshot(generation_dir) as snapshot:
        report = evaluate(scenarios, snapshot, BM25Policy(k=1))
    assert report["excluded"] == ["S-2"]  # no manual reference: not a retrieval hit or miss
    first, second = report["scenarios"][0]["turns"]
    assert first["recall"] == 0 and first["no_match"] is True  # turn 2 words not used early
    assert second["recall"] == 1 and second["covered"] == ["2"]
    assert report["summary"]["turns_scored"] == 2 and report["summary"]["mean_turn_recall"] == 0.5
    assert report["scenarios"][0]["union_recall"] == 1
    assert report["index"]["generation_id"] == report["data_generation"]
    assert json.dumps(report)  # serializable
