"""Validate the catalog CSVs (categories, products, promotions) and build the SQLite catalog.

Only these three operational tables are read. Customer and order files are never opened.

Money: prices are parsed with Decimal and stored as integer cents (`price_cents`).
Discounts are stored as integer basis points (`discount_basis_points`, 15% -> 1500).
No float is used for either.

Problems are split into:
- errors: the data can't be loaded safely (bad structure, ids, references, types);
- warnings: loadable data with a business ambiguity the agent must handle explicitly.
Messages reference table, CSV line and record id, never raw cell values.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

TABLES = ("categories", "products", "promotions")
REQUIRED_COLUMNS = {
    "categories": ("category_id", "name", "description"),
    "products": (
        "product_id",
        "price_brl",
        "name",
        "category_id",
        "description",
        "stock_quantity",
        "status",
        "specs",
        "created_at",
    ),
    "promotions": ("promotion_id", "product_id", "discount_percent", "description", "is_active"),
}
PRODUCT_STATUSES = ("active", "discontinued", "coming_soon")

_POSITIVE_INT = re.compile(r"[1-9][0-9]*")
_NON_NEGATIVE_INT = re.compile(r"0|[1-9][0-9]*")
_MONEY = re.compile(r"[0-9]+(\.[0-9]{1,2})?")
_PERCENT = re.compile(r"[0-9]+(\.[0-9]{1,2})?")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

SCHEMA = """
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY CHECK (category_id > 0),
    name TEXT NOT NULL CHECK (length(name) > 0),
    description TEXT NOT NULL
) STRICT;

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY CHECK (product_id > 0),
    name TEXT NOT NULL CHECK (length(name) > 0),
    category_id INTEGER NOT NULL REFERENCES categories (category_id),
    description TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
    specs_json TEXT NOT NULL CHECK (json_valid(specs_json) AND json_type(specs_json) = 'object'),
    created_at TEXT NOT NULL CHECK (date(created_at) = created_at)
) STRICT;

CREATE TABLE promotions (
    promotion_id INTEGER PRIMARY KEY CHECK (promotion_id > 0),
    product_id INTEGER NOT NULL REFERENCES products (product_id),
    discount_basis_points INTEGER NOT NULL CHECK (discount_basis_points BETWEEN 1 AND 9999),
    description TEXT NOT NULL,
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1))
) STRICT;

CREATE TABLE source_files (
    role TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    records INTEGER NOT NULL CHECK (records >= 0)
) STRICT;

CREATE INDEX products_by_category ON products (category_id);
CREATE INDEX promotions_by_product ON promotions (product_id);
"""


class DataPreparationError(Exception):
    """Raised when sources can't produce a valid artifact; nothing is replaced."""


@dataclass(frozen=True)
class Issue:
    code: str
    table: str
    message: str
    line: int | None = None
    record_id: int | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class SourceFile:
    role: str
    path: Path
    sha256: str


@dataclass
class Catalog:
    categories: list[dict] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    promotions: list[dict] = field(default_factory=list)
    sources: list[SourceFile] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    normalized_cells: int = 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def find_table_file(csv_dir: Path, table: str) -> Path:
    """Exactly one `<table>.csv` or `<prefix> - <table>.csv` in csv_dir."""
    matches = sorted(p for p in csv_dir.glob(f"*{table}.csv") if p.is_file())
    # `*products.csv` must not match e.g. `order_products.csv`: require a separator before the name.
    matches = [p for p in matches if p.name == f"{table}.csv" or p.name.endswith(f" {table}.csv")]
    if len(matches) != 1:
        raise DataPreparationError(
            f"Expected exactly one '{table}.csv' (or '<prefix> - {table}.csv') in the CSV "
            f"directory, found {len(matches)}."
        )
    return matches[0]


def parse_money_cents(text: str) -> int | None:
    """'599.9' -> 59990. None for anything that isn't a plain non-negative decimal."""
    if not _MONEY.fullmatch(text):
        return None
    cents = Decimal(text) * 100
    return int(cents) if cents == cents.to_integral_value() else None


def parse_basis_points(text: str) -> int | None:
    """'15' -> 1500, '12.5' -> 1250. None if not a plain decimal with at most 2 places."""
    if not _PERCENT.fullmatch(text):
        return None
    return int(Decimal(text) * 100)


def _read_rows(path: Path, table: str, catalog: Catalog) -> list[tuple[int, dict]]:
    """(line, row) pairs with peripheral whitespace stripped; structural problems -> errors."""
    rows: list[tuple[int, dict]] = []
    # utf-8-sig drops a leading BOM if present; newline='' lets csv handle CRLF and quoted fields.
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = header
        missing = [c for c in REQUIRED_COLUMNS[table] if c not in header]
        if missing:
            catalog.errors.append(
                Issue("missing_columns", table, f"Missing required columns: {', '.join(missing)}.")
            )
            return []
        if len(set(header)) != len(header):
            catalog.errors.append(
                Issue("duplicate_columns", table, "Header repeats a column name.")
            )
            return []
        extra = [c for c in header if c not in REQUIRED_COLUMNS[table]]
        if extra:
            catalog.warnings.append(
                Issue("ignored_columns", table, f"Columns not loaded: {', '.join(extra)}.")
            )
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                catalog.errors.append(
                    Issue(
                        "row_width",
                        table,
                        "Row has a different number of fields than the header.",
                        reader.line_num,
                    )
                )
                continue
            clean = {}
            for key, value in row.items():
                stripped = value.strip()
                if stripped != value:
                    catalog.normalized_cells += 1
                clean[key] = stripped
            rows.append((reader.line_num, clean))
    return rows


def _id(value: str, table: str, column: str, line: int, catalog: Catalog) -> int | None:
    if _POSITIVE_INT.fullmatch(value):
        return int(value)
    catalog.errors.append(
        Issue("invalid_id", table, f"'{column}' must be a positive integer.", line)
    )
    return None


def load_catalog(csv_dir: Path) -> Catalog:
    """Read and validate the three catalog tables. Never raises for data problems; see .errors."""
    catalog = Catalog()
    paths = {table: find_table_file(csv_dir, table) for table in TABLES}
    for table, path in paths.items():
        catalog.sources.append(SourceFile(table, path, sha256_file(path)))

    def error(code: str, table: str, message: str, line: int, record_id: int | None) -> None:
        catalog.errors.append(Issue(code, table, message, line, record_id))

    def warn(code: str, table: str, message: str, record_id: int | None = None) -> None:
        catalog.warnings.append(Issue(code, table, message, None, record_id))

    # categories
    seen: set[int] = set()
    for line, row in _read_rows(paths["categories"], "categories", catalog):
        cid = _id(row["category_id"], "categories", "category_id", line, catalog)
        if cid is None:
            continue
        if cid in seen:
            error("duplicate_id", "categories", "Duplicate category_id.", line, cid)
            continue
        seen.add(cid)
        if not row["name"]:
            error("required", "categories", "'name' is empty.", line, cid)
            continue
        if not row["description"]:
            warn("empty_description", "categories", "Category has no description.", cid)
        catalog.categories.append(
            {"category_id": cid, "name": row["name"], "description": row["description"]}
        )

    category_ids = {c["category_id"] for c in catalog.categories}

    # products
    seen = set()
    for line, row in _read_rows(paths["products"], "products", catalog):
        pid = _id(row["product_id"], "products", "product_id", line, catalog)
        if pid is None:
            continue
        if pid in seen:
            error("duplicate_id", "products", "Duplicate product_id.", line, pid)
            continue
        seen.add(pid)
        problems = len(catalog.errors)
        if not row["name"]:
            error("required", "products", "'name' is empty.", line, pid)
        category_id = (
            int(row["category_id"]) if _POSITIVE_INT.fullmatch(row["category_id"]) else None
        )
        if category_id is None:
            error(
                "invalid_reference",
                "products",
                "'category_id' must be a positive integer.",
                line,
                pid,
            )
        elif category_id not in category_ids:
            error(
                "unknown_reference",
                "products",
                "'category_id' is not a loaded category.",
                line,
                pid,
            )
        price_cents = parse_money_cents(row["price_brl"])
        if price_cents is None or price_cents <= 0:
            error(
                "invalid_price",
                "products",
                "'price_brl' must be a positive decimal with '.' and at most 2 decimal places.",
                line,
                pid,
            )
        if not _NON_NEGATIVE_INT.fullmatch(row["stock_quantity"]):
            error(
                "invalid_stock",
                "products",
                "'stock_quantity' must be a non-negative integer.",
                line,
                pid,
            )
        if row["status"] not in PRODUCT_STATUSES:
            error(
                "invalid_status",
                "products",
                f"'status' must be one of {', '.join(PRODUCT_STATUSES)}.",
                line,
                pid,
            )
        try:
            specs = json.loads(row["specs"])
        except json.JSONDecodeError:
            specs = None
            error("invalid_specs", "products", "'specs' is not valid JSON.", line, pid)
        else:
            if not isinstance(specs, dict):
                error("invalid_specs", "products", "'specs' must be a JSON object.", line, pid)
        try:
            valid_date = bool(_ISO_DATE.fullmatch(row["created_at"])) and bool(
                date.fromisoformat(row["created_at"])
            )
        except ValueError:
            valid_date = False
        if not valid_date:
            error("invalid_date", "products", "'created_at' must be a YYYY-MM-DD date.", line, pid)
        if len(catalog.errors) > problems:
            continue
        if not row["description"]:
            warn("empty_description", "products", "Product has no description.", pid)
        catalog.products.append(
            {
                "product_id": pid,
                "name": row["name"],
                "category_id": category_id,
                "description": row["description"],
                "price_cents": price_cents,
                "stock_quantity": int(row["stock_quantity"]),
                "status": row["status"],
                "specs_json": row["specs"],  # original text, validated as a JSON object
                "created_at": row["created_at"],
            }
        )

    products = {p["product_id"]: p for p in catalog.products}

    # promotions
    seen = set()
    for line, row in _read_rows(paths["promotions"], "promotions", catalog):
        promo_id = _id(row["promotion_id"], "promotions", "promotion_id", line, catalog)
        if promo_id is None:
            continue
        if promo_id in seen:
            error("duplicate_id", "promotions", "Duplicate promotion_id.", line, promo_id)
            continue
        seen.add(promo_id)
        problems = len(catalog.errors)
        product_id = int(row["product_id"]) if _POSITIVE_INT.fullmatch(row["product_id"]) else None
        if product_id is None:
            error(
                "invalid_reference",
                "promotions",
                "'product_id' must be a positive integer.",
                line,
                promo_id,
            )
        elif product_id not in products:
            error(
                "unknown_reference",
                "promotions",
                "'product_id' is not a loaded product.",
                line,
                promo_id,
            )
        bps = parse_basis_points(row["discount_percent"])
        if bps is None or not 0 < bps < 10000:
            error(
                "invalid_discount",
                "promotions",
                "'discount_percent' must be a decimal greater than 0 and less than 100.",
                line,
                promo_id,
            )
        if row["is_active"] not in ("0", "1"):
            error(
                "invalid_flag", "promotions", "'is_active' must be exactly 0 or 1.", line, promo_id
            )
        if len(catalog.errors) > problems:
            continue
        if not row["description"]:
            warn("empty_description", "promotions", "Promotion has no description.", promo_id)
        catalog.promotions.append(
            {
                "promotion_id": promo_id,
                "product_id": product_id,
                "discount_basis_points": bps,
                "description": row["description"],
                "is_active": int(row["is_active"]),
            }
        )

    _business_warnings(catalog, warn)
    return catalog


def _business_warnings(catalog: Catalog, warn) -> None:
    """Loadable data that the agent must not resolve by guessing."""
    names: dict[str, int] = {}
    for p in catalog.products:
        pid, status, stock = p["product_id"], p["status"], p["stock_quantity"]
        if status == "active" and stock == 0:
            warn("active_out_of_stock", "products", "Status 'active' with zero stock.", pid)
        if status == "discontinued" and stock > 0:
            warn(
                "discontinued_with_stock",
                "products",
                "Status 'discontinued' with positive stock.",
                pid,
            )
        if status == "coming_soon" and stock > 0:
            warn(
                "coming_soon_with_stock",
                "products",
                "Status 'coming_soon' with positive stock.",
                pid,
            )
        names[p["name"].casefold()] = names.get(p["name"].casefold(), 0) + 1
    for p in catalog.products:
        if names[p["name"].casefold()] > 1:
            warn(
                "duplicate_name", "products", "Another product has the same name.", p["product_id"]
            )

    used = {p["category_id"] for p in catalog.products}
    for c in catalog.categories:
        if c["category_id"] not in used:
            warn("unused_category", "categories", "Category has no products.", c["category_id"])
    category_names = [c["name"].casefold() for c in catalog.categories]
    for c in catalog.categories:
        if category_names.count(c["name"].casefold()) > 1:
            warn(
                "duplicate_name",
                "categories",
                "Another category has the same name.",
                c["category_id"],
            )

    products = {p["product_id"]: p for p in catalog.products}
    by_product: dict[int, list[dict]] = {}
    for promo in catalog.promotions:
        by_product.setdefault(promo["product_id"], []).append(promo)
        product = products[promo["product_id"]]
        if promo["is_active"] and (product["status"] != "active" or product["stock_quantity"] == 0):
            warn(
                "active_promotion_unavailable_product",
                "promotions",
                "Active promotion on a product that is not active or has zero stock.",
                promo["promotion_id"],
            )
    for product_id, promos in sorted(by_product.items()):
        active = [p for p in promos if p["is_active"]]
        if len(active) > 1:
            warn(
                "multiple_active_promotions",
                "products",
                "More than one active promotion; the manual says promotions are not cumulative.",
                product_id,
            )
        elif len(promos) > 1:
            warn(
                "multiple_promotion_rows",
                "products",
                "More than one promotion row (at most one active).",
                product_id,
            )


def build_database(catalog: Catalog, destination: Path) -> None:
    """Write to a temp file next to the destination, verify it, then atomically replace.

    A failure at any step leaves an existing destination file untouched.
    """
    if catalog.errors:
        raise DataPreparationError("Catalog has validation errors; database not written.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temp.unlink(missing_ok=True)
    try:
        connection = sqlite3.connect(temp)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA)
            with connection:
                connection.executemany(
                    "INSERT INTO categories VALUES (:category_id, :name, :description)",
                    catalog.categories,
                )
                connection.executemany(
                    "INSERT INTO products VALUES (:product_id, :name, :category_id, :description, "
                    ":price_cents, :stock_quantity, :status, :specs_json, :created_at)",
                    catalog.products,
                )
                connection.executemany(
                    "INSERT INTO promotions VALUES (:promotion_id, :product_id, "
                    ":discount_basis_points, :description, :is_active)",
                    catalog.promotions,
                )
                records = {
                    "categories": len(catalog.categories),
                    "products": len(catalog.products),
                    "promotions": len(catalog.promotions),
                }
                connection.executemany(
                    "INSERT INTO source_files VALUES (?, ?, ?, ?)",
                    [(s.role, s.path.name, s.sha256, records[s.role]) for s in catalog.sources],
                )
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DataPreparationError("SQLite integrity check failed.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise DataPreparationError("SQLite foreign key check failed.")
            for table, expected in records.items():
                if connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != expected:
                    raise DataPreparationError(f"Row count mismatch in {table}.")
        finally:
            connection.close()
        os.replace(temp, destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
