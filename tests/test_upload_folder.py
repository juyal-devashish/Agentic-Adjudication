"""Per-case upload folders with one ZIP (plan step 19)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pymupdf
import pytest

from adjud.paths import DataPaths
from adjud.runner import Runner
from adjud.upload_folder import ZipRefused, unpack
from adjud.workflow import State
from tools.reset_demo import reset

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "flow1-testkit" / "documents" / "DEMO01-101-0001_AE-001"


def pdf(text: str) -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), text)
        return doc.tobytes()


def zip_of(files: dict[str, bytes], *, encrypted: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    data = bytearray(buf.getvalue())
    if encrypted:  # set the "encrypted" bit in every local and central header
        for sig, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            start = 0
            while (i := data.find(sig, start)) >= 0:
                data[i + offset] |= 0x1
                start = i + 4
    return bytes(data)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.delenv("ADJUD_UPLOAD_DIR", raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(ROOT / "data" / "flow1-testkit")
    paths = DataPaths(data)
    reset(paths, preload=True)
    r = Runner(paths)
    r.run_once()
    return r


def test_every_waiting_case_gets_a_folder_named_after_it(runner):
    runner.take_uploads()
    folder = runner.paths.uploads / "DEMO01-0001"
    assert "DEMO01-0001" in (folder / "HOW_TO_UPLOAD.txt").read_text()
    assert len([p for p in runner.paths.uploads.iterdir() if p.is_dir()]) == 11


def test_a_complete_zip_is_imported_and_processed_without_the_ui(runner):
    runner.take_uploads()
    folder = runner.paths.uploads / "DEMO01-0001"
    files = {n: (DOCS / n).read_bytes() for n in ("discharge_summary.pdf", "ecg_report.pdf")}
    files["__MACOSX/._discharge_summary.pdf"] = b"resource fork"  # ignored
    (folder / "documents.zip").write_bytes(zip_of(files))
    runner.take_uploads()  # first sight: maybe still being written
    store = runner.pipeline.store
    assert store.state("DEMO01-0001").state == State.AWAITING_DOCUMENTS
    runner.take_uploads()  # unchanged since the last tick: complete
    assert store.state("DEMO01-0001").state == State.DEIDENTIFYING
    up = next(e for e in store.events("DEMO01-0001") if e["type"] == "documents.uploaded")
    assert up["actor"] == "system:upload-folder" and up["data"]["via"]["zip"] == "documents.zip"
    assert sorted(f["name"] for f in up["data"]["files"]) == list(sorted(
        ("discharge_summary.pdf", "ecg_report.pdf")))  # fmt: skip
    assert not (folder / "documents.zip").exists() and list((folder / "imported").glob("*.zip"))
    runner.run_case_steps()
    assert store.state("DEMO01-0001").state == State.AWAITING_SITE_CONFIRMATION


def test_a_zip_still_growing_waits(runner):
    runner.take_uploads()
    z = runner.paths.uploads / "DEMO01-0001" / "big.zip"
    z.write_bytes(zip_of({"a.pdf": pdf("Part one of a letter.")}))
    runner.take_uploads()
    z.write_bytes(zip_of({"a.pdf": pdf("Part one of a letter."), "b.pdf": pdf("More.")}))
    runner.take_uploads()  # changed since the last look: not yet
    assert runner.pipeline.store.state("DEMO01-0001").state == State.AWAITING_DOCUMENTS


@pytest.mark.parametrize(
    ("files", "kw", "reason"),
    [
        ({"a.pdf": b"x"}, {"encrypted": True}, "password-protected"),
        ({"../evil.pdf": b"x"}, {}, "unsafe path"),
        ({"inner.zip": b"PK"}, {}, "another archive"),
        ({}, {}, "empty"),
    ],
)
def test_unsafe_zips_are_refused_with_a_reason(files, kw, reason):
    with pytest.raises(ZipRefused, match=reason):
        unpack(zip_of(files, **kw))


def test_a_refused_zip_is_kept_logged_and_the_site_told(runner):
    runner.take_uploads()
    folder = runner.paths.uploads / "DEMO01-0001"
    (folder / "broken.zip").write_bytes(b"PK\x03\x04 not really a zip")
    runner.take_uploads()
    runner.take_uploads()
    store = runner.pipeline.store
    rejected = next(e for e in store.events("DEMO01-0001") if e["type"] == "upload.rejected")
    assert "damaged" in rejected["data"]["reason"]
    assert list((folder / "rejected").glob("*.reason.txt"))
    notes = [json.loads(p.read_text()) for p in runner.paths.outbox.glob("*.json")]
    assert any(n["template"] == "upload_rejected" for n in notes)
    assert store.state("DEMO01-0001").state == State.AWAITING_DOCUMENTS


def test_upload_dir_can_live_elsewhere(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("ADJUD_UPLOAD_DIR", str(tmp_path / "OneDrive" / "Adjudication uploads"))
    runner.take_uploads()
    assert (tmp_path / "OneDrive" / "Adjudication uploads" / "DEMO01-0001").is_dir()
