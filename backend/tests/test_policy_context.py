"""EMP-12 policy context: full-manual baseline unchanged, BM25 selection, index and metadata."""

import hashlib
import json
from pathlib import Path

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
from emporio_assistant.retrieval_eval import (
    BASIS,
    ReferenceKeyError,
    evaluate,
    evaluate_by_turn,
    load_references,
    user_digest,
)
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


# ---------------------------------------------------------------- by-turn references
BY_TURN_SCENARIOS = {
    "contract_version": 2,
    "scenarios": [
        {"id": "S-1", "category": "policies", "language": "en",
         "turns": [{"user": "zzqx"}, {"user": "test returns"}, {"user": "list products"}]},
    ],
}  # fmt: skip


def turn_entry(turn, user, required=(), support=(), needs=None, **extra):
    entry = {
        "scenario": "S-1",
        "turn": turn,
        "user_sha12": user_digest(user),
        "needs": needs or (["policy"] if required else ["catalog"]),
        "required": [{"any_of": list(g), "covers": "fact"} for g in required],
        "support": [{"section": s, "role": "related", "note": "n"} for s in support],
        "application_limits": [],
        "scored": bool(required),
        "rationale": "r",
        "basis": ["manual", "turn_message"],
    }
    return {**entry, **extra}


def by_turn_key(snapshot, turns):
    sha = hashlib.sha256(snapshot.generation.policies.read_bytes()).hexdigest()
    return {
        "version": "test-1",
        "scenario_contract_version": 2,
        "basis_allowed": sorted(BASIS),
        "curated_against": {"policies_sha256": sha},
        "turns": turns,
    }


def valid_turns():
    return [
        turn_entry(1, "zzqx", required=[["2"]]),
        turn_entry(2, "test returns", required=[["2", "1"], ["1.1"]]),
        turn_entry(3, "list products"),
    ]


def test_by_turn_scores_groups_and_leaves_catalog_turns_out(generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        turns = valid_turns()
        turns[0]["support"] = [{"section": "1.1", "role": "tool_rule", "note": "n"}]
        report = evaluate_by_turn(BY_TURN_SCENARIOS, by_turn_key(snapshot, turns), snapshot,
                                  BM25Policy(k=1))  # fmt: skip
    first, second, third = report["turns"]
    assert first["no_match"] is True and first["coverage"] == {
        "numerator": 0,
        "denominator": 1,
        "value": 0.0,
    }  # later words never used early
    assert second["sections"] == ["2"]  # either section of the first group is enough
    assert second["coverage"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert second["required"][0]["retrieved"] == ["2"]
    assert third["scored"] is False and "coverage" not in third  # neither hit nor miss
    summary = report["summary"]
    assert summary["turns_scored"] == 2 and summary["turns_not_applicable"] == 1
    assert summary["not_applicable_by_needs"] == {"catalog": 1}
    assert summary["required_groups_retrieved"] == {"numerator": 1, "denominator": 3,
                                                    "value": 0.3333}  # fmt: skip
    assert summary["support_retrieved_informational"]["denominator"] == 1  # not in coverage
    assert summary["mean_turn_coverage"] == {"sum": 0.5, "turns": 2, "value": 0.25}
    assert report["method"] == "by-turn" and report["references_version"] == "test-1"
    assert json.dumps(report)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda t: t.pop(), "missing turns"),
        (lambda t: t.append(dict(t[0])), "duplicate entry"),
        (lambda t: t[0].update(turn=9), "no such scenario turn"),
        (lambda t: t[0].update(user_sha12="000000000000"), "user message differs"),
        (lambda t: t[0].update(required=[{"any_of": ["9.9"], "covers": "x"}]), "unknown manual"),
        (lambda t: t[0].update(scored=False), "scored must be true"),
        (lambda t: t[2].update(needs=["policy"]), "needs 'policy'"),
        (lambda t: t[0].update(basis=["model_answer"]), "basis must be"),
        (lambda t: t[0].update(basis=["history"]), "no history at t1"),
        (
            lambda t: t[1].update(support=[{"section": "1.1", "role": "related", "note": "n"}]),
            "support repeats",
        ),
        (lambda t: t[2].update(application_limits=["x"]), "needs 'application_limit'"),
        (lambda t: t[0].update(extra=1), "fields must be"),
    ],
)
def test_by_turn_key_mismatches_are_refused(generation_dir, change, message):
    with CatalogSnapshot(generation_dir) as snapshot:
        turns = valid_turns()
        change(turns)
        with pytest.raises(ReferenceKeyError, match=message):
            evaluate_by_turn(BY_TURN_SCENARIOS, by_turn_key(snapshot, turns), snapshot,
                             BM25Policy(k=1))  # fmt: skip


def test_by_turn_key_curated_on_other_policies_is_refused(generation_dir):
    with CatalogSnapshot(generation_dir) as snapshot:
        key = by_turn_key(snapshot, valid_turns())
        key["curated_against"]["policies_sha256"] = "0" * 64
        with pytest.raises(ReferenceKeyError, match="different policies"):
            evaluate_by_turn(BY_TURN_SCENARIOS, key, snapshot, BM25Policy(k=1))


def test_versioned_key_matches_the_development_scenarios():
    root = Path(__file__).resolve().parents[2]
    scenarios = json.loads((root / "evals/development_scenarios.json").read_text("utf-8"))
    key = json.loads((root / "evals/policy_references_by_turn.json").read_text("utf-8"))
    entries = load_references(key, scenarios, None, None)  # manual checks need the generation
    assert len(scenarios["scenarios"]) == 30 and len(entries) == 44
