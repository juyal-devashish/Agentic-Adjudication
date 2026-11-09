"""The dossier (plan step 16): built once per passed QC, hashed, never changed."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from adjud.documents import upload
from adjud.dossier import latest_dossier
from adjud.integrity import verify_all
from adjud.paths import DataPaths
from adjud.review import submit
from adjud.runner import Runner
from tools.reset_demo import reset

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
DOCS = KIT / "documents" / "DEMO01-101-0001_AE-001"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    data = tmp_path_factory.mktemp("dossier") / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    files = ("troponin_results.xlsx", "ecg_report.pdf", "discharge_summary.pdf")
    upload(store, cid, [(n, (DOCS / n).read_bytes()) for n in files], "site101.coord")
    runner.run_case_steps()
    submit(store, cid, "site101.coord", "demo", paths.config)
    for _ in range(3):  # handoff + QC start, QC passes on the checks, dossier + narrative
        runner.run_case_steps()
    return store, cid


def test_dossier_has_cover_summary_and_documents_in_checklist_order(built):
    store, cid = built
    d = latest_dossier(store, cid)
    assert d["version"] == 1 and d["dossier"]["path"] == "dossier/dossier_v1.pdf"
    names = [p["name"] for p in d["page_map"]]
    assert names == ["discharge_summary.pdf", "ecg_report.pdf", "troponin_results.xlsx"]
    firsts = [p["first_page"] for p in d["page_map"]]
    assert firsts[0] >= 3 and firsts == sorted(firsts)
    with pymupdf.open(stream=store.get_file(cid, d["dossier"]["path"]), filetype="pdf") as doc:
        toc = [title for _, title, _ in doc.get_toc()]
        text = [page.get_text() for page in doc]
        meta = doc.metadata
    assert toc[:2] == ["Cover", "EDC summary"] and len(toc) == 5
    assert "Discharge or death summary: discharge_summary.pdf" in toc
    assert all("Subject DEMO01-101-0001" in t and f"Case {cid}" in t for t in text)
    assert "Myocardial infarction" in text[0] and "Aspirin" in "".join(text[1 : firsts[0] - 1])
    everything = "\n".join(text)
    for identifier in ("Roberts", "2929647", "24/07/1955"):  # seeded PHI of this subject
        assert identifier not in everything
    assert meta["author"] == "" and "dossier" in meta["title"].lower()


def test_dossier_is_hashed_and_tampering_is_detected(built):
    store, cid = built
    assert all(r.ok for r in verify_all(store.paths))
    path = store.case_dir(cid) / latest_dossier(store, cid)["dossier"]["path"]
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"%tampered")
        assert not all(r.ok for r in verify_all(store.paths))
    finally:
        path.write_bytes(original)


def test_flow3_handoff_lists_dossier_narrative_and_qc_with_matching_hashes(tmp_path, monkeypatch):
    import json

    from adjud.events import sha256_bytes
    from adjud.workflow import State

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    files = ("discharge_summary.pdf", "ecg_report.pdf", "troponin_results.xlsx")
    upload(store, cid, [(n, (DOCS / n).read_bytes()) for n in files], "site101.coord")
    runner.run_case_steps()
    submit(store, cid, "site101.coord", "demo", paths.config)
    for _ in range(4):  # handoff + QC start, QC, dossier + narrative, Flow 3 handoff
        runner.run_case_steps()
    assert store.state(cid).state == State.READY_FOR_ADJUDICATION
    event = next(e for e in store.events(cid) if e["type"] == "handoff.flow3_ready")
    manifest = json.loads((store.case_dir(cid) / event["data"]["manifest"]["path"]).read_text())
    for key in ("dossier", "narrative", "qc", "flow2_manifest"):
        ref = manifest[key]
        assert sha256_bytes((store.case_dir(cid) / ref["path"]).read_bytes()) == ref["sha256"]
    assert "documents/original" not in json.dumps(manifest)
    assert manifest["narrative"]["approved_by"] == "agent:narrative"
    assert manifest["narrative"]["simulated_ai"] is True
    notes = [json.loads(p.read_text()) for p in paths.outbox.glob("*.json")]
    assert any(n["template"] == "ready_for_adjudication" and n["case_id"] == cid for n in notes)
    runner.run_case_steps()  # nothing left to do: written once
    assert sum(e["type"] == "handoff.flow3_ready" for e in store.events(cid)) == 1
    assert all(r.ok for r in verify_all(paths))
