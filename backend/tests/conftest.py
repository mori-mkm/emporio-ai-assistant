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


def prepare_generation(csv_dir: Path, manual: Path, out: Path) -> Path:
    """Publish a synthetic generation with the real preparation command."""
    from emporio_assistant.prepare_data import main as prepare

    args = ["--csv-dir", str(csv_dir), "--manual", str(manual), "--output-dir", str(out)]
    assert prepare(args) == 0
    return out


@pytest.fixture
def generation_dir(csv_dir: Path, manual: Path, tmp_path: Path) -> Path:
    return prepare_generation(csv_dir, manual, tmp_path / "derived")


# ---- Fake Anthropic client: OFFLINE, returns real SDK Message objects, never opens a socket.

USAGE_OK = {
    "input_tokens": 1000,
    "output_tokens": 200,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
    "service_tier": "standard",
}


def message(content, stop_reason="end_turn", usage=None):
    from anthropic.types import Message

    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": USAGE_OK if usage is None else usage,
        }
    )


def text(value, usage=None):
    return message([{"type": "text", "text": value}], "end_turn", usage)


def tool_use(*calls, thinking=True):
    blocks = [{"type": "thinking", "thinking": "", "signature": "sig-abc"}] if thinking else []
    blocks += [
        {"type": "tool_use", "id": f"toolu_{i}_{name}", "name": name, "input": args}
        for i, (name, args) in enumerate(calls)
    ]
    return message(blocks, "tool_use")


class FakeAnthropic:
    """Scripted `client.messages.create`; items are Messages, exceptions or callables."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def create(self, **params):
        import copy

        self.requests.append(copy.deepcopy(params))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item(params) if callable(item) else item


@pytest.fixture
def live_ledger(tmp_path: Path):
    """A temporary live ledger for tests; the real ledger is never touched."""
    from emporio_assistant.budget import Ledger

    return Ledger.create(tmp_path / "ledger-test.sqlite", "live")


@pytest.fixture
def pricing():
    from emporio_assistant.pricing import load_price_table

    return load_price_table()  # versioned config: verified Haiku prices
