"""Shared test helpers: synthetic catalog copies and a small synthetic PDF manual."""

import csv
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_catalog"


@pytest.fixture
def csv_dir(tmp_path: Path) -> Path:
    """A writable copy of the synthetic catalog CSVs."""
    target = tmp_path / "sources with spaces é"
    shutil.copytree(FIXTURES, target)
    return target


def edit_csv(csv_dir: Path, table: str, edit) -> None:
    """Rewrite one synthetic table after `edit(header, rows)` mutates it in place."""
    path = next(csv_dir.glob(f"* {table}.csv"))
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    header, body = rows[0], rows[1:]
    edit(header, body)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([header, *body])


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(path: Path, pages: list[list[str]], outline: list[tuple[str, int, int]]) -> None:
    """Write a text PDF (Helvetica, WinAnsi) and add outline entries (title, page index, level)."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # pages tree, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for lines in pages:
        content = b"".join(
            b"BT /F1 11 Tf 72 %d Td (" % (760 - 16 * i) + _escape(line) + b") Tj ET\n"
            for i, line in enumerate(lines)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % len(objects)
        )
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(kids)

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    out.write(b"".join(b"%010d 00000 n \n" % o for o in offsets))
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )

    writer = PdfWriter(clone_from=BytesIO(out.getvalue()))
    parents: dict[int, object] = {}
    for title, page_index, level in outline:
        parents[level] = writer.add_outline_item(title, page_index, parent=parents.get(level - 1))
    with path.open("wb") as handle:
        writer.write(handle)


SYNTHETIC_PAGES = [
    ["SYNTHETIC TEST MANUAL", "Not the Empório manual"],
    [
        "Test Manual Header",
        "1. Test Payments",
        "Pix gives a test discount.",
        "1.1 Test Installments",
        "Up to 3 test installments.",
        "Página 2",
    ],
    [
        "Test Manual Header",
        "Continuation of installments on the next page.",
        "2. Test Returns",
        "Seven test days for returns.",
        "Página 3",
    ],
]
SYNTHETIC_OUTLINE = [
    ("1. Test Payments", 1, 1),
    ("1.1 Test Installments", 1, 2),
    ("2. Test Returns", 2, 1),
]


@pytest.fixture
def manual(tmp_path: Path) -> Path:
    path = tmp_path / "manual dir ã" / "synthetic manual.pdf"
    path.parent.mkdir()
    make_pdf(path, SYNTHETIC_PAGES, SYNTHETIC_OUTLINE)
    return path
