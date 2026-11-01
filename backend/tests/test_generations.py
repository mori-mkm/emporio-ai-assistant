"""Publication consistency: what a consumer resolves after successes and injected failures."""

import json
import os
import sqlite3
from pathlib import Path

import pytest
from conftest import edit_csv

from emporio_assistant import generations, prepare_data
from emporio_assistant.generations import GenerationError, active_generation
from emporio_assistant.prepare_data import main


def run(csv_dir: Path, manual: Path, out: Path) -> int:
    return main(["--csv-dir", str(csv_dir), "--manual", str(manual), "--output-dir", str(out)])


def snapshot(out: Path) -> tuple[str, dict[str, bytes]]:
    """What a consumer sees: the active generation id and the bytes of its three files."""
    active = active_generation(out)
    return active.id, {
        p.name: p.read_bytes() for p in (active.catalog, active.policies, active.quality_report)
    }


def catalog_rows(path: Path) -> list:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        return [
            connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            for t in ("products", "promotions")
        ]
    finally:
        connection.close()


def fail_when(monkeypatch, target, attribute: str, predicate):
    """Wrap target.attribute so it raises when predicate(*args) is true."""
    original = getattr(target, attribute)

    def wrapper(*args, **kwargs):
        if predicate(*args):
            raise OSError("injected failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, attribute, wrapper)


def test_success_resolves_all_three_files_from_one_generation(csv_dir, manual, tmp_path):
    out = tmp_path / "derived out"
    assert run(csv_dir, manual, out) == 0
    active = active_generation(out)
    assert {active.catalog.parent, active.policies.parent, active.quality_report.parent} == {
        active.directory
    }
    assert active.directory.parent == (out / "generations").resolve()
    report = json.loads(active.quality_report.read_text(encoding="utf-8"))
    assert report["counts"]["products"] == 4
    assert json.loads(active.policies.read_text(encoding="utf-8"))["sections"]
    assert len(catalog_rows(active.catalog)[0]) == 4


@pytest.mark.parametrize("failing_file", ["policies.json", "quality_report.json"])
def test_failure_writing_a_file_keeps_previous_generation(
    csv_dir, manual, tmp_path, monkeypatch, failing_file, capsys
):
    out = tmp_path / "derived"
    assert run(csv_dir, manual, out) == 0
    before = snapshot(out)

    # New sources would change the catalog; the write of one later file then fails.
    edit_csv(
        csv_dir,
        "products",
        lambda header, body: body[0].__setitem__(header.index("stock_quantity"), "7"),
    )
    fail_when(
        monkeypatch,
        prepare_data,
        "write_file_synced",
        lambda path, data: Path(path).name == failing_file,
    )
    assert run(csv_dir, manual, out) == 1

    assert snapshot(out) == before
    err = capsys.readouterr().err
    assert "FAILED while publishing" in err and "active generation was not changed" in err
    # The failure happened after the new catalog was written: an inactive generation remains.
    new = [d for d in (out / "generations").iterdir() if d.name != before[0]]
    assert len(new) == 1 and (new[0] / "catalog.sqlite").is_file()


def test_failure_in_final_promotion_keeps_previous_generation(
    csv_dir, manual, tmp_path, monkeypatch
):
    out = tmp_path / "derived"
    assert run(csv_dir, manual, out) == 0
    before = snapshot(out)

    edit_csv(
        csv_dir,
        "products",
        lambda header, body: body[0].__setitem__(header.index("stock_quantity"), "7"),
    )
    fail_when(monkeypatch, os, "replace", lambda src, dst: Path(dst).name == generations.MANIFEST)
    assert run(csv_dir, manual, out) == 1

    assert snapshot(out) == before
    assert not list(out.glob(".current.json.*.tmp"))
    # The new generation folder exists but is inactive.
    assert len(list((out / "generations").iterdir())) == 2


def test_failed_first_preparation_leaves_no_active_generation(
    csv_dir, manual, tmp_path, monkeypatch
):
    out = tmp_path / "derived"
    fail_when(
        monkeypatch,
        prepare_data,
        "write_file_synced",
        lambda path, data: Path(path).name == "policies.json",
    )
    assert run(csv_dir, manual, out) == 1
    assert not (out / generations.MANIFEST).exists()
    with pytest.raises(GenerationError, match="No active generation"):
        active_generation(out)


def test_rerun_with_same_sources_publishes_equal_content(csv_dir, manual, tmp_path):
    out = tmp_path / "derived"
    assert run(csv_dir, manual, out) == 0
    first = active_generation(out)
    first_rows, first_policies = catalog_rows(first.catalog), first.policies.read_bytes()

    assert run(csv_dir, manual, out) == 0
    second = active_generation(out)
    assert second.id != first.id
    assert catalog_rows(second.catalog) == first_rows
    assert second.policies.read_bytes() == first_policies
    assert first.directory.is_dir()  # previous generation is kept, not deleted


def test_invalid_sources_report_failure_and_keep_generation(csv_dir, manual, tmp_path, capsys):
    out = tmp_path / "derived"
    assert run(csv_dir, manual, out) == 0
    before = snapshot(out)
    edit_csv(csv_dir, "products", lambda header, body: body.append(list(body[0])))
    assert run(csv_dir, manual, out) == 1
    assert snapshot(out) == before
    err = capsys.readouterr().err
    assert "duplicate_id" in err and "active generation was not changed" in err


def test_operational_error_is_reported_not_raised(csv_dir, manual, tmp_path, capsys):
    out = tmp_path / "derived"
    out.mkdir()
    (out / "generations").write_text("a file where a folder is expected", encoding="utf-8")
    assert run(csv_dir, manual, out) == 1
    assert "FAILED while publishing" in capsys.readouterr().err


def test_output_inside_sources_is_refused(csv_dir, manual):
    assert run(csv_dir, manual, csv_dir / "out") == 1
    assert not (csv_dir / "out").exists()


def test_resolver_rejects_bad_manifests(csv_dir, manual, tmp_path):
    out = tmp_path / "derived"
    assert run(csv_dir, manual, out) == 0
    manifest_path = out / generations.MANIFEST
    good = json.loads(manifest_path.read_text(encoding="utf-8"))

    def broken(mutate):
        data = json.loads(json.dumps(good))
        mutate(data)
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(GenerationError):
            active_generation(out)

    broken(lambda d: d.update(generation="../../outside"))
    broken(lambda d: d["files"]["catalog"].update(name="../catalog.sqlite"))
    broken(lambda d: d["files"].pop("quality_report"))
    broken(lambda d: d["files"]["policies"].update(sha256="0" * 64))
    broken(lambda d: d.update(format_version=99))
    manifest_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(GenerationError):
        active_generation(out)

    manifest_path.write_text(json.dumps(good), encoding="utf-8")
    (active_generation(out).policies).unlink()  # incomplete generation
    with pytest.raises(GenerationError, match="incomplete"):
        active_generation(out)
