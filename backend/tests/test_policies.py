import hashlib

import pytest
from conftest import SYNTHETIC_PAGES, make_pdf

from emporio_assistant.catalog import DataPreparationError
from emporio_assistant.policies import extract_policies


def test_sections_keep_source_page_and_hash(manual):
    document = extract_policies(manual)
    assert document.source["file_name"] == "synthetic manual.pdf"  # name only, no local path
    assert document.source["sha256"] == hashlib.sha256(manual.read_bytes()).hexdigest()
    assert document.source["page_count"] == 3
    assert [p["page"] for p in document.pages] == [1, 2, 3]

    sections = {s["id"]: s for s in document.sections}
    assert list(sections) == ["front", "1", "1.1", "2"]
    assert sections["1"]["title"] == "Test Payments"
    assert "Pix gives a test discount." in sections["1"]["text"]
    # 1.1 starts on page 2 and continues on page 3, without the running header or page footer.
    installments = sections["1.1"]
    assert (installments["page_start"], installments["page_end"]) == (2, 3)
    assert "Continuation of installments" in installments["text"]
    assert "Test Manual Header" not in installments["text"]
    assert "Página" not in installments["text"]
    assert sections["2"]["page_start"] == 3


def test_missing_outline_heading_fails_closed(tmp_path):
    pages = [list(page) for page in SYNTHETIC_PAGES]
    pages[2][2] = "2. Something Else"  # outline still says "2. Test Returns"
    path = tmp_path / "broken.pdf"
    make_pdf(path, pages, [("1. Test Payments", 1, 1), ("2. Test Returns", 2, 1)])
    with pytest.raises(DataPreparationError):
        extract_policies(path)
