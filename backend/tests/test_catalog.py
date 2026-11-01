import hashlib
import sqlite3
from pathlib import Path

import pytest
from conftest import edit_csv

from emporio_assistant.catalog import (
    DataPreparationError,
    build_database,
    load_catalog,
    parse_basis_points,
    parse_money_cents,
)


def rows(db: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def codes(issues) -> set[str]:
    return {i.code for i in issues}


def test_money_and_discount_are_exact_integers():
    assert parse_money_cents("599.9") == 59990
    assert parse_money_cents("19567") == 1956700
    assert parse_money_cents("0.1") == 10
    for bad in ["12,50", "-10", "10.123", "1e3", "", "R$ 10"]:
        assert parse_money_cents(bad) is None
    assert parse_basis_points("15") == 1500
    assert parse_basis_points("12.5") == 1250


def test_synthetic_catalog_loads_with_expected_warnings(csv_dir, tmp_path):
    catalog = load_catalog(csv_dir)
    assert catalog.errors == []
    assert codes(catalog.warnings) == {
        "active_out_of_stock",
        "discontinued_with_stock",
        "coming_soon_with_stock",
        "unused_category",
        "multiple_promotion_rows",
    }
    db = tmp_path / "out" / "catalog.sqlite"
    build_database(catalog, db)
    assert rows(db, "SELECT product_id, price_cents FROM products ORDER BY 1") == [
        (1001, 123450),
        (1002, 99900),
        (1003, 1956700),
        (1004, 15990),
    ]
    assert rows(db, "SELECT DISTINCT typeof(price_cents) FROM products") == [("integer",)]
    assert rows(db, "SELECT promotion_id, discount_basis_points FROM promotions ORDER BY 1") == [
        (1, 1500),
        (2, 1250),
        (3, 1000),
    ]
    # Only catalog tables: no customers, orders or order items.
    tables = {r[0] for r in rows(db, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"categories", "products", "promotions", "source_files"}
    # Source provenance: file name and hash only, no machine path.
    sources = rows(db, "SELECT role, file_name, sha256 FROM source_files ORDER BY 1")
    products_csv = next(csv_dir.glob("* products.csv"))
    assert (
        "products",
        products_csv.name,
        hashlib.sha256(products_csv.read_bytes()).hexdigest(),
    ) in sources


def test_reload_is_idempotent(csv_dir, tmp_path):
    db = tmp_path / "catalog.sqlite"
    build_database(load_catalog(csv_dir), db)
    first = rows(db, "SELECT * FROM products ORDER BY 1") + rows(
        db, "SELECT * FROM promotions ORDER BY 1"
    )
    build_database(load_catalog(csv_dir), db)
    second = rows(db, "SELECT * FROM products ORDER BY 1") + rows(
        db, "SELECT * FROM promotions ORDER BY 1"
    )
    assert first == second
    assert rows(db, "SELECT count(*) FROM products") == [(4,)]


def _set(table: str, record_index: int, column: str, value: str):
    def edit(header, body):
        body[record_index][header.index(column)] = value

    return table, edit


def _duplicate_first_row(table: str):
    return table, lambda header, body: body.append(list(body[0]))


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        (_duplicate_first_row("products"), "duplicate_id"),
        (_duplicate_first_row("categories"), "duplicate_id"),
        (_set("products", 0, "category_id", "99"), "unknown_reference"),
        (_set("promotions", 0, "product_id", "9999"), "unknown_reference"),
        (_set("products", 0, "price_brl", "12,50"), "invalid_price"),
        (_set("products", 0, "price_brl", "0"), "invalid_price"),
        (_set("products", 0, "price_brl", "-5"), "invalid_price"),
        (_set("products", 0, "stock_quantity", "-1"), "invalid_stock"),
        (_set("products", 0, "stock_quantity", "2.5"), "invalid_stock"),
        (_set("products", 0, "specs", '{"body": "test"'), "invalid_specs"),
        (_set("products", 0, "specs", '["not", "an", "object"]'), "invalid_specs"),
        (_set("products", 0, "status", "available"), "invalid_status"),
        (_set("products", 0, "created_at", "2024-02-30"), "invalid_date"),
        (_set("promotions", 0, "is_active", "true"), "invalid_flag"),
        (_set("promotions", 0, "discount_percent", "100"), "invalid_discount"),
        (
            ("products", lambda header, body: header.__setitem__(header.index("status"), "state")),
            "missing_columns",
        ),
        (("products", lambda header, body: body[0].append("extra")), "row_width"),
    ],
)
def test_invalid_data_is_an_error(csv_dir, change, expected_code):
    table, edit = change
    edit_csv(csv_dir, table, edit)
    catalog = load_catalog(csv_dir)
    assert expected_code in codes(catalog.errors)
    with pytest.raises(DataPreparationError):
        build_database(catalog, csv_dir.parent / "never.sqlite")
    assert not (csv_dir.parent / "never.sqlite").exists()


def test_failed_load_keeps_previous_valid_database(csv_dir, tmp_path):
    db = tmp_path / "catalog.sqlite"
    build_database(load_catalog(csv_dir), db)
    before = db.read_bytes()

    # A catalog that passes validation but violates a schema constraint mid-write.
    catalog = load_catalog(csv_dir)
    catalog.promotions[0]["discount_basis_points"] = 0
    with pytest.raises(sqlite3.IntegrityError):
        build_database(catalog, db)

    assert db.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_peripheral_whitespace_is_stripped_and_counted(csv_dir):
    edit_csv(
        csv_dir,
        "products",
        lambda header, body: body[0].__setitem__(header.index("name"), "  Test Guitar A "),
    )
    catalog = load_catalog(csv_dir)
    assert catalog.errors == []
    assert catalog.products[0]["name"] == "Test Guitar A"
    assert catalog.normalized_cells == 1
