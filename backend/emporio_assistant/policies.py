"""Extract the store manual (PDF) page by page, with sections taken from the PDF outline.

Extraction runs locally with pypdf in "layout" mode: the default mode splits this
Google Docs export into one word per line and flattens its tables, while layout
mode keeps lines and table columns in reading order.

The output keeps the extracted text as is (only trailing spaces and blank-line runs
are normalized). It holds no interpretation: ambiguities are documented separately.
The SHA-256 identifies which bytes were used; it does not prove the text is correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import Destination

from emporio_assistant.catalog import DataPreparationError, sha256_file

_PAGE_FOOTER = re.compile(r"Página \d+")
_NUMBERED_TITLE = re.compile(r"(\d+(?:\.\d+)*)\.?\s+(.+)")
_MIN_PAGE_CHARS = 40


@dataclass
class PolicyDocument:
    source: dict
    pages: list[dict] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "pages": self.pages,
            "sections": self.sections,
            "warnings": self.warnings,
        }


def _normalize(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue  # collapse blank-line runs, drop leading blanks
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return out


def _outline(reader: PdfReader) -> list[tuple[int, str, int]]:
    """Flatten the outline into (level, title, 1-based page)."""
    items: list[tuple[int, str, int]] = []

    def walk(nodes, level: int) -> None:
        for node in nodes:
            if isinstance(node, list):
                walk(node, level + 1)
            elif isinstance(node, Destination):
                items.append(
                    (
                        level,
                        " ".join(node.title.split()),
                        reader.get_destination_page_number(node) + 1,
                    )
                )

    walk(reader.outline, 1)
    return items


def _join(lines: list[str]) -> str:
    """Join section lines, dropping blank-line runs and common indentation."""
    kept = _normalize("\n".join(lines))
    indent = min((len(x) - len(x.lstrip()) for x in kept if x.strip()), default=0)
    return "\n".join(x[indent:] for x in kept)


def extract_policies(pdf_path: Path) -> PolicyDocument:
    reader = PdfReader(pdf_path)
    metadata = reader.metadata or {}
    document = PolicyDocument(
        source={
            "file_name": pdf_path.name,
            "sha256": sha256_file(pdf_path),
            "page_count": len(reader.pages),
            "pdf_title": metadata.get("/Title"),
            "extractor": f"pypdf {version('pypdf')} (extraction_mode='layout')",
        }
    )

    page_lines: list[list[str]] = []
    for number, page in enumerate(reader.pages, start=1):
        lines = _normalize(page.extract_text(extraction_mode="layout") or "")
        text = "\n".join(lines)
        document.pages.append({"page": number, "text": text})
        page_lines.append(lines)
        if len(text.strip()) < _MIN_PAGE_CHARS:
            document.warnings.append(
                f"Page {number} has almost no extractable text (scanned or blank?)."
            )
        if "�" in text:
            document.warnings.append(
                f"Page {number} contains replacement characters (lost glyphs)."
            )

    # Running header/footer: lines repeated on most pages, and "Página N" footers.
    counts: dict[str, int] = {}
    for lines in page_lines:
        for line in {x.strip() for x in lines if x.strip()}:
            counts[line] = counts.get(line, 0) + 1
    running = {
        line for line, n in counts.items() if len(page_lines) > 2 and n > len(page_lines) / 2
    }

    body: list[tuple[int, str]] = [
        (number, line)
        for number, lines in enumerate(page_lines, start=1)
        for line in lines
        if line.strip() not in running and not _PAGE_FOOTER.fullmatch(line.strip())
    ]

    outline = _outline(reader)
    if not outline:
        document.warnings.append("PDF has no outline; sections were not identified, only pages.")
        return document

    # Locate each outline heading, in order, on the page its outline entry points to.
    starts: list[int] = []
    cursor = 0
    for _level, title, page in outline:
        index = next(
            (
                i
                for i in range(cursor, len(body))
                if body[i][0] == page and " ".join(body[i][1].split()) == title
            ),
            None,
        )
        if index is None:
            raise DataPreparationError(
                f"Outline heading '{title}' was not found on page {page}; "
                "extraction is not trustworthy."
            )
        starts.append(index)
        cursor = index + 1

    def section(
        section_id: str, title: str | None, level: int, begin: int, end: int, heading_page: int
    ) -> dict:
        content = body[begin:end]
        pages = [p for p, line in content if line.strip()] or [heading_page]
        return {
            "id": section_id,
            "title": title,
            "level": level,
            "page_start": min(pages + [heading_page]),
            "page_end": max(pages + [heading_page]),
            "text": _join([line for _, line in content]),
        }

    if starts[0] > 0 and any(line.strip() for _, line in body[: starts[0]]):
        document.sections.append(section("front", None, 0, 0, starts[0], body[0][0]))
    for i, (level, title, page) in enumerate(outline):
        match = _NUMBERED_TITLE.fullmatch(title)
        section_id = match.group(1) if match else f"outline-{i + 1}"
        end = starts[i + 1] if i + 1 < len(outline) else len(body)
        document.sections.append(
            section(section_id, match.group(2) if match else title, level, starts[i] + 1, end, page)
        )
    ids = [s["id"] for s in document.sections]
    if len(set(ids)) != len(ids):
        raise DataPreparationError("Outline produces duplicate section ids.")
    return document
