"""Which store-manual sections go into the system prompt (EMP-12).

Contract: `select(snapshot, history, user_text) -> PolicySelection`, called once per customer
turn. `FullManual` is the baseline (every section, prompt unchanged). `BM25Policy` keeps the
top-k sections ranked by SQLite FTS5 `bm25()` over the sections already extracted for the
same data generation. The score is lexical relevance, not factual confidence. No section is
added silently: when nothing matches, the selection is empty and says so (no fallback).

The index is a derived file outside the generation folder:
`<data_dir>/indexes/policies-bm25-<generation id>-<policies sha256[:12]>.sqlite`, rebuilt
when its recorded generation, policies hash or index format differ. It never touches the
catalog or the generation files.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from .tools import CatalogSnapshot, fold

INDEX_FORMAT = 1
TOKENIZER = "unicode61 remove_diacritics 2"  # case- and accent-insensitive, no stemming
TITLE_WEIGHT, BODY_WEIGHT = 2.0, 1.0  # bm25() column weights: titles are short and on-topic
MAX_QUERY_TERMS = 32
# Small closed list of Portuguese/English function words; everything else is kept.
STOPWORDS = frozenset(
    """a o as os um uma uns umas de do da dos das em no na nos nas por pelo pela para pra pro
    com sem e ou que se me te lhe eu voce voces ele ela eles elas nos meu minha seu sua isso
    isto esse essa este esta aquele aquela ja mas mais muito tem ter ha foi ser sao esta estou
    como qual quais quando onde porque entao the an and or of to in on for is are be it you i
    my me we do does can what which how""".split()
)


@dataclass(frozen=True)
class PolicySelection:
    mode: str  # full | bm25
    section_ids: tuple[str, ...]  # in manual order; exactly what the prompt contains
    details: dict = field(default_factory=dict)

    def record(self, sections: dict[str, dict]) -> dict:
        """Diagnostics for the run report (never sent to the API client)."""
        chars = sum(len(sections[i]["text"]) for i in self.section_ids)
        if self.mode == "full":
            return {"mode": "full", "section_count": len(self.section_ids), "chars": chars}
        return {"mode": self.mode, "sections": list(self.section_ids), "chars": chars,
                **self.details}  # fmt: skip


class FullManual:
    """Baseline: the whole manual, rendered exactly as before EMP-12."""

    mode = "full"

    def describe(self) -> dict:
        return {"mode": "full"}

    def select(self, snapshot: CatalogSnapshot, history: list[dict], user_text: str):
        ids = tuple(s["id"] for s in snapshot.policies()["sections"])
        return PolicySelection("full", ids)


def query_terms(text: str) -> list[str]:
    """Folded word tokens without function words, unique, in order, bounded. Each becomes a
    quoted FTS5 string, so operators (AND, NOT, NEAR, *, column:) are plain words."""
    terms, seen = [], set()
    for token in re.findall(r"\w+", fold(text)):
        if len(token) < 2 or token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) == MAX_QUERY_TERMS:
            break
    return terms


def match_expression(terms: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in terms)  # \w+ tokens contain no quote


def _parent(section_id: str) -> str | None:
    return section_id.rsplit(".", 1)[0] if "." in section_id else None


class BM25Policy:
    """Top-k manual sections for the current message plus the previous user messages of the
    same conversation (assistant text is not used). Expansion rule: a retrieved subsection
    (e.g. 3.1) also brings its parent section (3) when the parent has text, because parents
    hold the general rule or table their subsections qualify; every expansion is recorded."""

    mode = "bm25"

    def __init__(self, k: int = 4, history_user_turns: int = 2, index_dir: Path | None = None):
        if not 1 <= k <= 20 or not 0 <= history_user_turns <= 12:
            raise ValueError("k must be 1-20 and history_user_turns 0-12")
        self.k, self.history_user_turns, self.index_dir = k, history_user_turns, index_dir
        self._lock = threading.Lock()
        self._ready: dict[tuple[str, str], Path] = {}  # (policies path, sha) -> index file
        self.last_build_ms: int | None = None

    def describe(self) -> dict:
        return {
            "mode": "bm25",
            "k": self.k,
            "history_user_turns": self.history_user_turns,
            "tokenizer": TOKENIZER,
            "weights": {"title": TITLE_WEIGHT, "body": BODY_WEIGHT},
            "expansion": "parent section with text",
            "index_format": INDEX_FORMAT,
        }

    # ------------------------------------------------------------------ index
    def index_path(self, snapshot: CatalogSnapshot) -> Path:
        """Builds the index when missing or stale; returns its path."""
        policies = snapshot.generation.policies
        sha = hashlib.sha256(policies.read_bytes()).hexdigest()
        key = (str(policies), sha)
        with self._lock:
            if key in self._ready and self._ready[key].exists():
                return self._ready[key]
            directory = self.index_dir or policies.parents[2] / "indexes"
            path = directory / f"policies-bm25-{snapshot.id}-{sha[:12]}.sqlite"
            if not self._valid(path, snapshot.id, sha):
                self._build(path, snapshot, sha)
            self._ready[key] = path
            return path

    @staticmethod
    def _valid(path: Path, generation_id: str, sha: str) -> bool:
        if not path.exists():
            return False
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        except sqlite3.Error:
            return False
        expected = {
            "format": str(INDEX_FORMAT),
            "generation_id": generation_id,
            "policies_sha256": sha,
            "tokenizer": TOKENIZER,
        }
        return all(meta.get(k) == v for k, v in expected.items())

    def _build(self, path: Path, snapshot: CatalogSnapshot, sha: str) -> None:
        started = time.monotonic()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp-{os.getpid()}")
        tmp.unlink(missing_ok=True)
        sections = snapshot.policies()["sections"]
        with closing(sqlite3.connect(tmp)) as db, db:
            db.execute(f"CREATE VIRTUAL TABLE sections USING fts5(id UNINDEXED, title, body, "
                       f"tokenize='{TOKENIZER}')")  # fmt: skip
            db.executemany(
                "INSERT INTO sections (id, title, body) VALUES (?, ?, ?)",
                [(s["id"], s["title"] or "Capa", s["text"]) for s in sections],
            )
            build_ms = round((time.monotonic() - started) * 1000)
            db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [
                    ("format", str(INDEX_FORMAT)),
                    ("generation_id", snapshot.id),
                    ("policies_sha256", sha),
                    ("tokenizer", TOKENIZER),
                    ("sections", str(len(sections))),
                    ("build_ms", str(build_ms)),
                ],
            )
        os.replace(tmp, path)
        self.last_build_ms = build_ms

    # ------------------------------------------------------------------ query
    def select(self, snapshot: CatalogSnapshot, history: list[dict], user_text: str):
        previous = [m["content"] for m in history if m.get("role") == "user"]
        recent = previous[-self.history_user_turns :] if self.history_user_turns else []
        terms = query_terms(" ".join([*recent, user_text]))
        path = self.index_path(snapshot)
        details = {
            "index": path.name,
            "k": self.k,
            "query_terms": len(terms),
            "history_user_turns_used": len(recent),
        }
        started = time.monotonic()
        hits: list[tuple[str, float]] = []
        if terms:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                rows = db.execute(
                    "SELECT id, bm25(sections, 0.0, ?, ?) AS score FROM sections "
                    "WHERE sections MATCH ? ORDER BY score, id LIMIT ?",
                    (TITLE_WEIGHT, BODY_WEIGHT, match_expression(terms), self.k),
                ).fetchall()
            hits = [(r[0], round(-r[1], 4)) for r in rows]  # FTS5 bm25: lower is better
        details["query_ms"] = round((time.monotonic() - started) * 1000, 2)

        by_id = {s["id"]: s for s in snapshot.policies()["sections"]}
        chosen = {section_id for section_id, _ in hits}
        expanded = []
        for section_id, _ in hits:
            parent = _parent(section_id)
            if parent in by_id and parent not in chosen and by_id[parent]["text"].strip():
                chosen.add(parent)
                expanded.append({"section": parent, "parent_of": section_id})
        details["hits"] = [{"section": s, "score": score} for s, score in hits]
        details["expanded"] = expanded
        details["no_match"] = not hits
        if not terms:
            details["no_match_reason"] = "no query terms"
        elif not hits:
            details["no_match_reason"] = "no section matched"
        ordered = tuple(s["id"] for s in snapshot.policies()["sections"] if s["id"] in chosen)
        return PolicySelection("bm25", ordered, details)


POLICIES = {"full": FullManual, "bm25": BM25Policy}
