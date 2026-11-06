"""Headless smoke tests of the Streamlit pages (streamlit.testing AppTest)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adjud.paths import DataPaths
from tools.reset_demo import reset

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "ui" / "app.py")


@pytest.fixture
def demo(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data).mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    monkeypatch.setenv("ADJUD_DATA_DIR", str(data))
    paths = DataPaths(data)
    reset(paths, preload=True)
    return paths


def run(page: str | None = None, **state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    if page:
        at.switch_page(page)
        at.run()
    assert not at.exception, at.exception
    return at


def test_app_without_edc_asks_for_a_reset(tmp_path, monkeypatch):
    (tmp_path / "config").symlink_to(ROOT / "data" / "config")
    monkeypatch.setenv("ADJUD_DATA_DIR", str(tmp_path))
    at = run()
    assert any("reset_demo" in w.value for w in at.warning)


def test_import_now_from_page_1_creates_cases(demo):
    at = run(role="study_admin")
    assert any("SYNTHETIC DATA ONLY" in e.value for e in at.sidebar.error)
    import_now = next(b for b in at.button if b.label == "Import now")
    import_now.click().run()
    assert not at.exception, at.exception
    assert any("ACCEPTED" in s.value and "11 new cases" in s.value for s in at.success)
    assert len([p for p in demo.cases.iterdir() if p.is_dir()]) == 11


def test_site_user_cannot_import(demo):
    at = run(role="site")
    assert next(b for b in at.button if b.label == "Import now").disabled


def test_results_and_documents_pages_render_for_a_case(demo):
    from adjud.runner import Runner

    Runner(demo).run_once()
    case_id = sorted(p.name for p in demo.cases.iterdir() if p.is_dir())[0]
    at = run("pages/results.py", role="safety_specialist", case_id=case_id)
    assert any("Chain verified" in s.value for s in at.success)
    run("pages/site_documents.py", role="safety_specialist", case_id=case_id)


def test_site_flow_upload_process_review_and_sign(demo):
    from adjud.documents import upload
    from adjud.runner import Runner

    runner = Runner(demo)
    runner.run_once()
    pipe = runner.pipeline
    cid = pipe.case_for("DEMO01|DEMO01-101-0001|AE-001")
    kit_docs = ROOT / "data" / "flow1-testkit" / "documents" / "DEMO01-101-0001_AE-001"
    upload(
        pipe.store,
        cid,
        [(n, (kit_docs / n).read_bytes()) for n in ("ecg_report.pdf", "troponin_results.xlsx")],
        "site101.coord",
    )

    at = run("pages/site_documents.py", role="site", case_id=cid)
    next(b for b in at.button if b.label == "Process now").click().run()
    assert not at.exception, at.exception
    assert "AWAITING_SITE_CONFIRMATION" in [m.value for m in at.metric]

    at.text_input[-1].input("wrong")  # the signing form's password field
    next(b for b in at.button if b.label == "Sign and submit").click().run()
    assert any("Not submitted" in e.value for e in at.error)
    at.text_input[-1].input("demo")
    next(b for b in at.button if b.label == "Sign and submit").click().run()
    assert not at.exception, at.exception
    assert pipe.store.state(cid).state.value == "SUBMITTED"

    runner.run_case_steps()  # writes the handoff
    at = run("pages/results.py", role="safety_specialist", case_id=cid)
    assert any("Signed by Site 101 Coordinator" in s.value for s in at.success)
