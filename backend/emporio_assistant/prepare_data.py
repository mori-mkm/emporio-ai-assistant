"""Prepare local data artifacts from the challenge sources.

    python -m emporio_assistant.prepare_data --csv-dir DIR --manual FILE --output-dir DIR

Writes to the output directory (each file replaced atomically, only after every
source validated):
- catalog.sqlite       categories, products, promotions (no customers or orders)
- policies.json        store manual by page and section, with source hash
- quality_report.json  counts, warnings and ambiguities; no personal data

Exit code 0 on success, 1 when the sources fail validation (nothing replaced).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from emporio_assistant.catalog import DataPreparationError, build_database, load_catalog
from emporio_assistant.policies import extract_policies

CATALOG_FILE = "catalog.sqlite"
POLICIES_FILE = "policies.json"
REPORT_FILE = "quality_report.json"


def _write_json_atomic(data: dict, destination: Path) -> None:
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def prepare(csv_dir: Path, manual: Path, output_dir: Path) -> dict:
    """Validate everything first, then replace the artifacts. Returns the quality report."""
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

    output_dir.mkdir(parents=True, exist_ok=True)
    build_database(catalog, output_dir / CATALOG_FILE)
    _write_json_atomic(policies.as_dict(), output_dir / POLICIES_FILE)
    _write_json_atomic(report, output_dir / REPORT_FILE)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate challenge sources and build local data artifacts."
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        required=True,
        help="Folder with the categories/products/promotions CSVs.",
    )
    parser.add_argument("--manual", type=Path, required=True, help="Store policy manual PDF.")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Folder for the generated artifacts."
    )
    args = parser.parse_args(argv)
    try:
        report = prepare(args.csv_dir, args.manual, args.output_dir)
    except DataPreparationError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        print("No artifact was replaced.", file=sys.stderr)
        return 1
    counts = report["counts"]
    print(f"OK: wrote {CATALOG_FILE}, {POLICIES_FILE}, {REPORT_FILE} to {args.output_dir}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
