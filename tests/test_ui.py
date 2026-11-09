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


def html_text(at: AppTest) -> str:
    """Text of every st.html block (banner, case heading, rail, verdicts)."""
    return "\n".join(h.proto.body for h in at.get("html"))


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
    at = run(role="data_manager")
    assert "Synthetic data only." in html_text(at)
    import_now = next(b for b in at.button if b.label == "Import queued files")
    import_now.click().run()
    assert not at.exception, at.exception
    assert any(
        "Imported 01_initial.csv: 22 records, 11 new cases, 11 not eligible." in s.value
        for s in at.success
    )
    assert len([p for p in demo.cases.iterdir() if p.is_dir()]) == 11


@pytest.mark.parametrize("role", ["site", "safety_specialist"])
def test_only_the_data_manager_can_import(demo, role):
    at = run(role=role)
    assert next(b for b in at.button if b.label == "Import queued files").disabled
    assert next(b for b in at.button if b.label == "Import file").disabled
    assert "Only the data manager can enter an AE by hand." in [m.value for m in at.markdown]


def test_results_and_documents_pages_render_for_a_case(demo):
    from adjud.runner import Runner

    Runner(demo).run_once()
    case_id = sorted(p.name for p in demo.cases.iterdir() if p.is_dir())[0]
    at = run("views/results.py", role="safety_specialist", case_id=case_id)
    assert "Intact" in html_text(at) and "Case " + case_id in html_text(at)
    run("views/site_documents.py", role="safety_specialist", case_id=case_id)


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

    at = run("views/site_documents.py", role="site", case_id=cid)
    assert 'aria-current="step">Removing identifiers' in html_text(at)
    next(b for b in at.button if b.label == "Remove identifiers now").click().run()
    assert not at.exception, at.exception
    assert pipe.store.state(cid).state.value == "AWAITING_SITE_CONFIRMATION"
    assert 'aria-current="step">Site review' in html_text(at)
    assert "Check the redacted pages, then sign and submit." in html_text(at)

    at.text_input[-1].input("wrong")  # the signing form's password field
    next(b for b in at.button if b.label == "Sign and submit").click().run()
    assert any("Not signed: the password is wrong" in e.value for e in at.error)
    at.text_input[-1].input("demo")
    next(b for b in at.button if b.label == "Sign and submit").click().run()
    assert not at.exception, at.exception
    assert pipe.store.state(cid).state.value == "SUBMITTED"

    runner.run_case_steps()  # writes the handoff
    at = run("views/results.py", role="safety_specialist", case_id=cid)
    assert any("Signed by Site 101 Coordinator" in m.value for m in at.markdown)


def test_reset_keeps_corpus_metrics_and_stages_demo_files(demo):
    (demo.metrics).mkdir(exist_ok=True)
    (demo.metrics / "latest.json").write_text("{}")
    reset(demo)
    assert (demo.metrics / "latest.json").exists()
    assert (demo.root / "demo_uploads" / "inbox_files" / "01_initial.csv").exists()
    assert (demo.root / "demo_uploads" / "documents" / "DEMO01-101-0001_AE-001").is_dir()
    assert not any(demo.inbox.iterdir())
