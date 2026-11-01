"""Prepare local data artifacts from the challenge sources.

    python -m emporio_assistant.prepare_data --csv-dir DIR --manual FILE --output-dir DIR

Validates every source first, writes a new generation folder under
`<output-dir>/generations/`, checks it, then makes it active by replacing
`<output-dir>/current.json` once (see `generations.py`):
- catalog.sqlite       categories, products, promotions (no customers or orders)
- policies.json        store manual by page and section, with source hash
- quality_report.json  counts, warnings and ambiguities; no personal data

Consumers resolve files with `generations.active_generation(output_dir)`.
Exit code 0 on success; 1 when sources fail validation or publication fails
(the previously active generation is unchanged).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from emporio_assistant.catalog import DataPreparationError, build_database, load_catalog
from emporio_assistant.generations import (
    FILES,
    Generation,
    GenerationError,
    create_generation_dir,
    promote,
    write_file_synced,
)
from emporio_assistant.policies import extract_policies

LEGACY_FILES = tuple(FILES.values())  # pre-generation layout wrote these to the output root


def _write_json(data: dict, destination: Path) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    write_file_synced(destination, text.encode("utf-8"))


def _check_generation(directory: Path, report: dict) -> None:
    """Read the written files back before they can become active."""
    for name in (FILES["policies"], FILES["quality_report"]):
        json.loads((directory / name).read_text(encoding="utf-8"))
    uri = (directory / FILES["catalog"]).as_uri() + "?mode=ro"  # percent-encodes spaces/accents
    connection = sqlite3.connect(uri, uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DataPreparationError("Written catalog failed the integrity check.")
        for table in ("categories", "products", "promotions"):
            count = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if count != report["counts"][table]:
                raise DataPreparationError(f"Written catalog has an unexpected {table} count.")
    finally:
        connection.close()


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def prepare(csv_dir: Path, manual: Path, output_dir: Path) -> tuple[Generation, dict]:
    """Validate, write a generation, check it, promote it. Returns (active generation, report)."""
    csv_dir, manual, output_dir = csv_dir.resolve(), manual.resolve(), output_dir.resolve()
    if not csv_dir.is_dir():
        raise DataPreparationError("--csv-dir is not a directory.")
    if not manual.is_file():
        raise DataPreparationError("--manual is not a file.")
    for source_folder in {csv_dir, manual.parent}:
        if _inside(output_dir, source_folder):
            raise DataPreparationError("--output-dir must be outside the source folders.")

    catalog = load_catalog(csv_dir)
    if catalog.errors:
        raise DataPreparationError(
            "Catalog validation failed:\n"
            + "\n".join(f"  {json.dumps(e.as_dict(), ensure_ascii=False)}" for e in catalog.errors)
        )
    policies = extract_policies(manual)

    report = {
        "sources": [
            {"role": s.role, "file_name": s.path.name, "sha256": s.sha256} for s in catalog.sources
        ]
        + [{"role": "manual", "file_name": manual.name, "sha256": policies.source["sha256"]}],
        "counts": {
            "categories": len(catalog.categories),
            "products": len(catalog.products),
            "promotions": len(catalog.promotions),
            "active_promotions": sum(p["is_active"] for p in catalog.promotions),
            "manual_pages": policies.source["page_count"],
            "manual_sections": len(policies.sections),
        },
        "normalization": {
            "encoding": "UTF-8 (a leading BOM is accepted and dropped)",
            "cells_with_peripheral_whitespace_stripped": catalog.normalized_cells,
            "money": "price_brl parsed as Decimal, stored as integer price_cents",
            "discounts": "discount_percent as Decimal, stored as integer discount_basis_points",
        },
        "errors": [],
        "warnings": [w.as_dict() for w in catalog.warnings],
        "manual_warnings": policies.warnings,
        "excluded_sources": "customers, orders and order_items are not read or loaded",
    }

    generation_id, directory = create_generation_dir(output_dir)
    build_database(catalog, directory / FILES["catalog"])
    _write_json(policies.as_dict(), directory / FILES["policies"])
    _write_json(report, directory / FILES["quality_report"])
    _check_generation(directory, report)
    return promote(output_dir, generation_id), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate challenge sources and publish a new data generation."
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        required=True,
        help="Folder with the categories/products/promotions CSVs.",
    )
    parser.add_argument("--manual", type=Path, required=True, help="Store policy manual PDF.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Folder holding generations/ and current.json.",
    )
    args = parser.parse_args(argv)
    try:
        generation, report = prepare(args.csv_dir, args.manual, args.output_dir)
    except DataPreparationError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        print("The active generation was not changed.", file=sys.stderr)
        return 1
    except (GenerationError, OSError, sqlite3.Error) as exc:
        print(f"FAILED while publishing: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "The active generation was not changed. An inactive folder may remain under "
            "generations/; it is never used and can be deleted manually.",
            file=sys.stderr,
        )
        return 1

    counts = report["counts"]
    print(f"OK: active generation {generation.id} in {args.output_dir}")
    print(
        f"  categories={counts['categories']} products={counts['products']} "
        f"promotions={counts['promotions']} (active={counts['active_promotions']}) "
        f"manual pages={counts['manual_pages']} sections={counts['manual_sections']}"
    )
    print(f"  warnings={len(report['warnings'])} manual_warnings={len(report['manual_warnings'])}")
    for warning in report["warnings"]:
        print(
            f"    - {warning['code']} [{warning['table']} {warning.get('record_id', '')}]: "
            f"{warning['message']}"
        )
    for warning in report["manual_warnings"]:
        print(f"    - manual: {warning}")
    legacy = [name for name in LEGACY_FILES if (args.output_dir / name).exists()]
    if legacy:
        print(
            f"  note: {', '.join(legacy)} in the output root come from the old layout; "
            "they are not used and can be deleted manually."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
