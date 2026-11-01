"""Publish prepared data as one consistent generation, and resolve the active one.

Layout inside the output directory:

    generations/<id>/catalog.sqlite
    generations/<id>/policies.json
    generations/<id>/quality_report.json
    current.json          <- manifest naming the active generation and each file's SHA-256

A generation folder is written and checked first; it becomes active only when
`current.json` is replaced in a single `os.replace`. Until then consumers keep
resolving the previous generation. A failed run can leave an inactive generation
folder behind; it is never referenced by the manifest.

Guarantee: the manifest switch is atomic for readers on the same volume. Files are
flushed with fsync before the switch, but no claim is made about surviving a power
loss at the exact moment of the rename (Windows offers no directory fsync).
Old generations are never deleted automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MANIFEST = "current.json"
GENERATIONS_DIR = "generations"
FORMAT_VERSION = 1
FILES = {
    "catalog": "catalog.sqlite",
    "policies": "policies.json",
    "quality_report": "quality_report.json",
}
_GENERATION_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{6}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class GenerationError(Exception):
    """The active generation is missing, invalid or incomplete."""


@dataclass(frozen=True)
class Generation:
    id: str
    directory: Path
    catalog: Path
    policies: Path
    quality_report: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def write_file_synced(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def create_generation_dir(output_dir: Path) -> tuple[str, Path]:
    """A new, empty, not-yet-active generation folder."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    generation_id = f"{stamp}-{secrets.token_hex(3)}"
    directory = output_dir / GENERATIONS_DIR / generation_id
    directory.mkdir(parents=True, exist_ok=False)
    return generation_id, directory


def promote(output_dir: Path, generation_id: str) -> Generation:
    """Make a complete generation active by atomically replacing the manifest."""
    directory = output_dir / GENERATIONS_DIR / generation_id
    files = {}
    for role, name in FILES.items():
        path = directory / name
        if not path.is_file():
            raise GenerationError(f"Generation {generation_id} is missing {name}; not promoted.")
        files[role] = {"name": name, "sha256": _sha256(path)}
    manifest = {"format_version": FORMAT_VERSION, "generation": generation_id, "files": files}
    temp = output_dir / f".{MANIFEST}.{os.getpid()}.tmp"
    try:
        write_file_synced(temp, (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        os.replace(temp, output_dir / MANIFEST)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return active_generation(output_dir)


def active_generation(output_dir: Path) -> Generation:
    """Resolve all three artifacts from one read of the manifest, verifying each file's hash."""
    output_dir = output_dir.resolve()
    try:
        manifest = json.loads((output_dir / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GenerationError("No active generation: run the data preparation command.") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Unreadable manifest {MANIFEST}: {exc}") from None

    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
        raise GenerationError("Manifest has an unsupported format.")
    generation_id = manifest.get("generation")
    if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
        raise GenerationError("Manifest has an invalid generation id.")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(FILES):
        raise GenerationError("Manifest must list exactly the catalog, policies and report files.")

    root = (output_dir / GENERATIONS_DIR).resolve()
    directory = (root / generation_id).resolve()
    if directory.parent != root:
        raise GenerationError("Manifest points outside the generations folder.")
    paths = {}
    for role, expected_name in FILES.items():
        entry = files[role]
        if not isinstance(entry, dict) or entry.get("name") != expected_name:
            raise GenerationError(f"Manifest entry for {role} must be named {expected_name}.")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise GenerationError(f"Manifest entry for {role} has an invalid sha256.")
        path = directory / expected_name
        if not path.is_file():
            raise GenerationError(
                f"Active generation {generation_id} is incomplete: no {expected_name}."
            )
        if _sha256(path) != digest:
            raise GenerationError(
                f"{expected_name} in generation {generation_id} does not match the manifest."
            )
        paths[role] = path
    return Generation(generation_id, directory, **paths)
