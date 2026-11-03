"""Freeze manifest for the reserved validation run (EMP-15 preparation).

The owner writes a local, git-ignored manifest after committing the candidate; the validation
run recomputes the same fields and refuses to start (before any client exists) when one
differs. The manifest is never versioned: a tracked file cannot name the commit that holds it.
Commands are in docs/evaluation.md ("Reserved validation run").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from .generations import active_generation
from .tools import DEFAULT_DATA_DIR

REPO = Path(__file__).resolve().parents[2]
FREEZE_FORMAT = 1
RUBRIC_HEADING = "## Human rubric (development)"
# Git-ignored places inside the repository; anything else must be outside it.
PRIVATE_DIRS = (".local", ".private", "evals/holdout")


def private_path(path: Path) -> bool:
    """Outside the repository, or inside one of its git-ignored private folders."""
    path = path.resolve()
    if not path.is_relative_to(REPO):
        return True
    return any(path.is_relative_to(REPO / d) for d in PRIVATE_DIRS)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rubric_sha256() -> str:
    text = (REPO / "docs" / "evaluation.md").read_text(encoding="utf-8")
    start = text.index(RUBRIC_HEADING)
    end = text.find("\n## ", start + len(RUBRIC_HEADING))
    return hashlib.sha256(text[start : end if end > 0 else None].encode("utf-8")).hexdigest()


def _source_hashes(catalog: Path) -> dict:
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as connection:
        return dict(connection.execute("SELECT role, sha256 FROM source_files ORDER BY role"))


def current_state(
    *,
    prompt_cache: str,
    policy_context: str,
    max_model_calls: int,
    scenarios: Path | None,
    data_dir: Path | None = None,
    scenarios_bytes: bytes | None = None,
) -> dict:
    """Everything the validation run must keep equal. Builds no client, reads no credential.
    `scenarios_bytes`: content the caller already read (and will run); else read once here."""
    from .agent import HISTORY_FORMAT, AgentConfig, config_version
    from .anthropic_adapter import AnthropicGenerator, GenerationSettings
    from .policy_context import POLICIES
    from .runner import _git_state

    class Probe:  # describe() of the configured adapter without creating its client
        mode = "live"
        describe = AnthropicGenerator.describe
        settings = GenerationSettings(prompt_cache=prompt_cache)

    policy = POLICIES[policy_context]()
    config = AgentConfig(max_model_calls=max_model_calls)
    # same data folder the backend reads (Settings.from_env)
    data_dir = data_dir or Path(os.environ.get("EMPORIO_DATA_DIR") or DEFAULT_DATA_DIR)
    generation = active_generation(data_dir)
    criteria = {"rubric_sha256": _rubric_sha256(), "suite": "reserved-validation"}
    if scenarios_bytes is None and scenarios is not None:
        scenarios_bytes = scenarios.read_bytes()
    if scenarios_bytes is not None:  # hash and contract from the same bytes
        criteria["validation_file_sha256"] = hashlib.sha256(scenarios_bytes).hexdigest()
        criteria["contract_version"] = json.loads(scenarios_bytes).get("contract_version")
    data = {
        "generation": generation.id,
        "sources_sha256": _source_hashes(generation.catalog),
        "catalog_sha256": _sha256(generation.catalog),
        "policies_sha256": _sha256(generation.policies),
    }
    if policy.mode == "embeddings":  # provider-made vectors: the exact index file is frozen
        from .tools import CatalogSnapshot

        with CatalogSnapshot(data_dir) as snapshot:
            index = policy.index_path(snapshot)
            # a stale or partial file would fail every answer: pin only a compatible index
            usable = index.exists() and policy._compatible(index, snapshot) is not None
        data["policy_index"] = index.name
        data["policy_index_sha256"] = _sha256(index) if usable else None
    return {
        "freeze_format": FREEZE_FORMAT,
        "git": _git_state(),
        "config_version": config_version(Probe(), config, policy),
        "configuration": {
            "generator": Probe().describe(),
            "agent": asdict(config),
            "policy_context": policy.describe(),
            "history_format": HISTORY_FORMAT,
        },
        "versions": {
            "python": platform.python_version(),
            "anthropic": version("anthropic"),
            "openai": version("openai"),  # embeddings client (and the GPT adapter)
            "pydantic": version("pydantic"),
        },
        "data": data,
        "criteria": criteria,
    }


def _flat(node: object, prefix: str = "") -> dict:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            out.update(_flat(value, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: node}


def divergences(manifest: dict, current: dict) -> list[str]:
    """Paths whose values differ (the manifest's own `written_at` is ignored)."""
    frozen = _flat({k: v for k, v in manifest.items() if k != "written_at"})
    now = _flat(current)
    return [
        f"{path}: frozen {frozen.get(path)!r} != current {now.get(path)!r}"
        for path in sorted(set(frozen) | set(now))
        if frozen.get(path) != now.get(path)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check the validation freeze manifest.")
    parser.add_argument("action", choices=["write", "check"])
    parser.add_argument("--out", type=Path, help="write: new manifest path (git-ignored)")
    parser.add_argument("--manifest", type=Path, help="check: existing manifest")
    parser.add_argument("--prompt-cache", choices=["off", "system-5m"], required=True)
    from .agent import AgentConfig
    from .policy_context import POLICIES

    parser.add_argument("--policy-context", choices=sorted(POLICIES), default="full")
    parser.add_argument("--max-model-calls", type=int, default=AgentConfig.max_model_calls)
    parser.add_argument("--scenarios", type=Path, default=None, help="owner-held validation file")
    args = parser.parse_args(argv)
    state = current_state(
        prompt_cache=args.prompt_cache,
        policy_context=args.policy_context,
        max_model_calls=args.max_model_calls,
        scenarios=args.scenarios,
    )
    if args.action == "write":
        out = args.out
        if out is None or not private_path(out) or out.exists():
            print("FAILED: --out must be a new file outside the repository or in a private folder")
            return 1
        if "policy_index_sha256" in state["data"] and not state["data"]["policy_index_sha256"]:
            print("FAILED: build the embeddings index first (retrieval_eval --policy embeddings)")
            return 1
        if state["git"].get("dirty") is not False:
            print("FAILED: commit the candidate first (working tree differs from HEAD)")
            return 1
        manifest = {**state, "written_at": datetime.now(UTC).isoformat(timespec="seconds")}
        out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"manifest written: {out} (config {state['config_version']})")
        return 0
    if args.manifest is None:
        print("FAILED: --manifest is required")
        return 1
    diffs = divergences(json.loads(args.manifest.read_bytes()), state)
    print("\n".join(diffs) if diffs else "frozen configuration matches")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
