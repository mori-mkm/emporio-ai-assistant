"""Which store-manual sections go into the system prompt (EMP-12).

Contract: `select(snapshot, history, user_text, context=None) -> PolicySelection`, called
once per customer turn inside the turn's time limit (`context` carries the run, budget and
remaining time for selectors that make paid calls). `FullManual` is the baseline (every
section, prompt unchanged). `BM25Policy` keeps the top-k sections ranked by SQLite FTS5
`bm25()` over the sections already extracted for the same data generation. `EmbeddingPolicy`
(experimental) ranks the same sections by cosine similarity of OpenAI embeddings, with the
same query, k and parent expansion. Scores rank sections; they are not factual confidence.
No section is added silently: there is no fallback to the full manual.

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

from .generation import ProviderError
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
    mode: str  # full | bm25 | embeddings
    section_ids: tuple[str, ...]  # in manual order; exactly what the prompt contains
    details: dict = field(default_factory=dict)
    op_ids: tuple[str, ...] = ()  # paid ledger operations made to select (embeddings query)

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

    def select(self, snapshot: CatalogSnapshot, history: list[dict], user_text: str, context=None):
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


def recent_user_text(history: list[dict], user_text: str, turns: int) -> tuple[list[str], str]:
    """The retrieval query of every selector: up to `turns` previous customer messages plus
    the current one (assistant text, records and expectations are never used)."""
    previous = [m["content"] for m in history if m.get("role") == "user"]
    recent = previous[-turns:] if turns else []
    return recent, user_text


def expand_and_order(snapshot: CatalogSnapshot, hit_ids: list[str]) -> tuple[tuple, list]:
    """Shared expansion rule: a retrieved subsection also brings its parent section when the
    parent has text. Returns the ids in manual order and the recorded expansions."""
    sections = snapshot.policies()["sections"]
    by_id = {s["id"]: s for s in sections}
    chosen, expanded = set(hit_ids), []
    for section_id in hit_ids:
        parent = _parent(section_id)
        if parent in by_id and parent not in chosen and by_id[parent]["text"].strip():
            chosen.add(parent)
            expanded.append({"section": parent, "parent_of": section_id})
    return tuple(s["id"] for s in sections if s["id"] in chosen), expanded


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
    def select(self, snapshot: CatalogSnapshot, history: list[dict], user_text: str, context=None):
        recent, user_text = recent_user_text(history, user_text, self.history_user_turns)
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

        ordered, expanded = expand_and_order(snapshot, [section_id for section_id, _ in hits])
        details["hits"] = [{"section": s, "score": score} for s, score in hits]
        details["expanded"] = expanded
        details["no_match"] = not hits
        if not terms:
            details["no_match_reason"] = "no query terms"
        elif not hits:
            details["no_match_reason"] = "no section matched"
        return PolicySelection("bm25", ordered, details)


EMBEDDING_INDEX_FORMAT = 1
QUERY_CACHE_SIZE = 256  # in memory only: query vectors are never written to disk


class IndexUnavailable(ProviderError):
    """No compatible embeddings index for this generation: build it first (paid, explicit).
    Raised before any paid query; the API answers with its generic provider error."""

    def __init__(self, detail: str):
        super().__init__("retrieval_index_unavailable", detail)


def section_input(section: dict) -> str:
    """The text embedded for a section: the same title and body BM25 indexes."""
    return f"{section['title'] or 'Capa'}\n{section['text']}"


class EmbeddingPolicy:
    """Top-k manual sections by cosine similarity between the query embedding and stored
    section embeddings (same corpus, query, k and parent expansion as BM25). No rewriter,
    reranker, hybrid search or fallback to the full manual. The index is built only by
    `build_index` (an explicit, budget-guarded step); at answer time a missing or
    incompatible index is refused. The similarity ranks sections; it is not confidence."""

    mode = "embeddings"

    def __init__(self, embedder=None, k: int = 4, history_user_turns: int = 2,
                 index_dir: Path | None = None):  # fmt: skip
        from .embeddings import DIMENSIONS, MODEL, PROVIDER

        if not 1 <= k <= 20 or not 0 <= history_user_turns <= 12:
            raise ValueError("k must be 1-20 and history_user_turns 0-12")
        self.embedder, self.k, self.history_user_turns = embedder, k, history_user_turns
        self.index_dir = index_dir
        self.provider, self.model, self.dimensions = PROVIDER, MODEL, DIMENSIONS
        if embedder is not None and (embedder.model, embedder.dimensions) != (MODEL, DIMENSIONS):
            raise ValueError("the embedder does not match the configured model and dimension")
        self._lock = threading.Lock()
        self._loaded: dict[str, list[tuple[str, list[float]]]] = {}  # index file -> vectors
        self._queries: dict[str, list[float]] = {}  # bounded, insertion-ordered
        self.cache_hits = self.cache_misses = 0

    def describe(self) -> dict:
        return {
            "mode": "embeddings",
            "provider": self.provider,
            "model": self.model,
            "dimensions": self.dimensions,
            "k": self.k,
            "history_user_turns": self.history_user_turns,
            "similarity": "cosine (ranking only, not factual confidence)",
            "section_input": "title + newline + body (same corpus as BM25)",
            "query": "previous customer messages (up to history_user_turns) + current, newline",
            "expansion": "parent section with text",
            "index_format": EMBEDDING_INDEX_FORMAT,
        }

    # ------------------------------------------------------------------ index
    def index_path(self, snapshot: CatalogSnapshot) -> Path:
        policies = snapshot.generation.policies
        sha = hashlib.sha256(policies.read_bytes()).hexdigest()
        directory = self.index_dir or policies.parents[2] / "indexes"
        name = f"policies-emb-{snapshot.id}-{sha[:12]}-{self.model}-{self.dimensions}.sqlite"
        return directory / name

    def _expected(self, snapshot: CatalogSnapshot) -> dict[str, str]:
        """section id -> sha256 of the exact text embedded for it."""
        return {s["id"]: hashlib.sha256(section_input(s).encode("utf-8")).hexdigest()
                for s in snapshot.policies()["sections"]}  # fmt: skip

    def _read(self, path: Path) -> tuple[dict, list]:
        """(meta, [(section id, content sha, vector)]) of a file with this format, model and
        dimension; empty when unreadable, different or holding an invalid vector."""
        from .embeddings import unpack

        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
                rows = db.execute("SELECT section_id, content_sha256, vector FROM vectors")
                rows = rows.fetchall()
        except sqlite3.Error:
            return {}, []
        same = {"format": str(EMBEDDING_INDEX_FORMAT), "model": self.model,
                "dimensions": str(self.dimensions)}  # fmt: skip
        if any(meta.get(k) != v for k, v in same.items()):
            return {}, []
        out = []
        for section_id, content_sha, blob in rows:
            vector = unpack(blob, self.dimensions)
            if vector is None:
                return {}, []
            out.append((section_id, content_sha, vector))
        return meta, out

    def _compatible(self, path: Path, snapshot: CatalogSnapshot) -> list | None:
        """[(section id, vector)] in manual order when the file matches this generation,
        policies, model, dimension and every section's content; None otherwise."""
        meta, rows = self._read(path)
        sha = hashlib.sha256(snapshot.generation.policies.read_bytes()).hexdigest()
        if meta.get("generation_id") != snapshot.id or meta.get("policies_sha256") != sha:
            return None
        expected = self._expected(snapshot)
        if {sid: c for sid, c, _ in rows} != expected or len(rows) != len(expected):
            return None
        vectors = {sid: v for sid, _, v in rows}
        return [(sid, vectors[sid]) for sid in expected]

    def build_index(self, snapshot: CatalogSnapshot, context) -> dict:
        """Builds (paid) or reuses the index. Vectors are reused by content key (model,
        dimension, sha256 of the embedded text) from earlier compatible index files, so a
        new generation with the same manual text embeds nothing."""
        from .embeddings import pack

        if self.embedder is None:
            raise IndexUnavailable("no embedder configured")
        path = self.index_path(snapshot)
        started = time.monotonic()
        if self._compatible(path, snapshot) is not None:
            return {"index": path.name, "built": False, "embedded": 0, "reused": None,
                    "op_ids": [], "ms": round((time.monotonic() - started) * 1000)}  # fmt: skip
        expected = self._expected(snapshot)
        reusable: dict[str, list[float]] = {}
        pattern = f"policies-emb-*-{self.model}-{self.dimensions}.sqlite"
        for other in sorted(path.parent.glob(pattern)) if path.parent.exists() else []:
            reusable.update({c: v for _, c, v in self._read(other)[1]})
        sections = {s["id"]: s for s in snapshot.policies()["sections"]}
        missing = [sid for sid, c in expected.items() if c not in reusable]
        op_ids = []
        if missing:
            vectors, op_id = self.embedder.embed([section_input(sections[s]) for s in missing],
                                                 context)  # fmt: skip
            op_ids.append(op_id)
            reusable.update({expected[s]: v for s, v in zip(missing, vectors, strict=True)})
        build_ms = round((time.monotonic() - started) * 1000)
        sha = hashlib.sha256(snapshot.generation.policies.read_bytes()).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp-{os.getpid()}")
        tmp.unlink(missing_ok=True)
        with closing(sqlite3.connect(tmp)) as db, db:
            db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE vectors (section_id TEXT PRIMARY KEY, "
                       "content_sha256 TEXT NOT NULL, vector BLOB NOT NULL)")  # fmt: skip
            db.executemany("INSERT INTO vectors VALUES (?, ?, ?)",
                           [(s, c, pack(reusable[c])) for s, c in expected.items()])  # fmt: skip
            db.executemany("INSERT INTO meta VALUES (?, ?)", [
                ("format", str(EMBEDDING_INDEX_FORMAT)), ("generation_id", snapshot.id),
                ("policies_sha256", sha), ("provider", self.provider), ("model", self.model),
                ("dimensions", str(self.dimensions)), ("sections", str(len(expected))),
                ("embedded", str(len(missing))), ("reused", str(len(expected) - len(missing))),
                ("build_ms", str(build_ms)), ("build_op_ids", ",".join(op_ids)),
            ])  # fmt: skip
        os.replace(tmp, path)
        return {"index": path.name, "built": True, "embedded": len(missing),
                "reused": len(expected) - len(missing), "op_ids": op_ids,
                "ms": build_ms}  # fmt: skip

    def _vectors(self, snapshot: CatalogSnapshot) -> tuple[Path, list]:
        path = self.index_path(snapshot)
        with self._lock:
            if str(path) not in self._loaded:
                vectors = self._compatible(path, snapshot) if path.exists() else None
                if vectors is None:
                    raise IndexUnavailable(f"no compatible embeddings index ({path.name})")
                self._loaded[str(path)] = vectors
            return path, self._loaded[str(path)]

    # ------------------------------------------------------------------ query
    def select(self, snapshot: CatalogSnapshot, history: list[dict], user_text: str, context=None):
        from .embeddings import cosine

        if self.embedder is None or context is None:
            raise IndexUnavailable("the embeddings policy needs an embedder and a call context")
        path, sections = self._vectors(snapshot)  # refused before any paid query
        recent, user_text = recent_user_text(history, user_text, self.history_user_turns)
        query = "\n".join([*recent, user_text])
        key = hashlib.sha256(f"{self.model}|{self.dimensions}|{query}".encode()).hexdigest()
        started = time.monotonic()
        op_ids: tuple[str, ...] = ()
        with self._lock:
            vector = self._queries.get(key)
        if vector is None:
            (vector,), op_id = self.embedder.embed([query], context)
            op_ids = (op_id,)
            with self._lock:
                self.cache_misses += 1
                self._queries[key] = vector
                while len(self._queries) > QUERY_CACHE_SIZE:
                    self._queries.pop(next(iter(self._queries)))
        else:
            with self._lock:
                self.cache_hits += 1
        position = {sid: n for n, (sid, _) in enumerate(sections)}
        scored = sorted(((cosine(vector, v), sid) for sid, v in sections),
                        key=lambda item: (-item[0], position[item[1]]))  # fmt: skip
        hits = scored[: self.k]
        ordered, expanded = expand_and_order(snapshot, [sid for _, sid in hits])
        details = {
            "index": path.name,
            "k": self.k,
            "query_chars": len(query),
            "history_user_turns_used": len(recent),
            "query_cache_hit": not op_ids,
            "query_ms": round((time.monotonic() - started) * 1000, 2),
            "hits": [{"section": sid, "similarity": round(s, 4)} for s, sid in hits],
            "expanded": expanded,
            "no_match": not hits,
        }
        return PolicySelection("embeddings", ordered, details, op_ids)


POLICIES = {"full": FullManual, "bm25": BM25Policy, "embeddings": EmbeddingPolicy}
