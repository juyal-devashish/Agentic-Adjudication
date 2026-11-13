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


def test_email_link_opens_the_case_as_a_user_of_its_site(demo):
    from adjud.runner import Runner

    Runner(demo).run_once()
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["role"] = "site"
    at.query_params["case"] = "DEMO01-0005"  # site 102
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["user_site"]["id"] == "site102.coord"
    assert at.session_state["case_id"] == "DEMO01-0005"


def test_case_record_lists_ai_calls_with_their_source(demo, tmp_path):
    import json

    from adjud.ai.config import AiConfig
    from adjud.ai.functions import DOCUMENT_TYPE
    from adjud.ai.gateway import Gateway, input_hash
    from adjud.ai.tools import Toolbox
    from adjud.runner import Runner

    runner = Runner(demo)
    runner.run_once()
    cid = runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    cfg = AiConfig(mode="replay", functions={"document_type": True})
    payload = {"file": "x.pdf"}
    fixture = (
        tmp_path
        / "replays"
        / "document_type"
        / f"{input_hash(DOCUMENT_TYPE, cfg.model, payload)}.json"
    )
    fixture.parent.mkdir(parents=True)
    answer = {"document_type": "other", "confidence": 0.5,
              "evidence": {"file": "x.pdf", "page": 1, "quote": "none"}}  # fmt: skip
    fixture.write_text(json.dumps({"simulated": True, "output": answer}))
    Gateway(runner.pipeline.store, cfg, tmp_path / "replays").run(
        cid, DOCUMENT_TYPE, payload, Toolbox()
    )
    at = run("views/results.py", role="safety_specialist", case_id=cid)
    table = next(df.value for df in at.dataframe if "Source" in df.value.columns)
    assert table.iloc[0]["Source"] == "Simulated AI" and table.iloc[0]["Task"] == "Document type"
    assert any("AI is off for this study" in m.value for m in at.markdown)


def test_quality_check_page_lets_the_safety_specialist_pass_and_sign_the_narrative(demo):
    import pymupdf

    from adjud.documents import upload
    from adjud.review import submit
    from adjud.runner import Runner

    runner = Runner(demo)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    files = []
    for name, text in (
        ("summary.pdf", "DISCHARGE SUMMARY. Chest pain."),
        ("ecg.pdf", "ECG REPORT. Sinus rhythm."),
    ):
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), text)
            files.append((name, doc.tobytes()))
    upload(store, cid, files, "site101.coord")  # complete, but no subject ID: a person decides
    runner.run_case_steps()
    submit(store, cid, "site101.coord", "demo", demo.config)
    for _ in range(3):  # handoff, QC start, QC round
        runner.run_case_steps()

    at = run("views/quality_check.py", role="safety_specialist", case_id=cid)
    assert 'aria-current="step">Quality check' in html_text(at)
    at.text_input[-1].input("demo")
    next(b for b in at.button if b.label == "Sign decision").click().run()
    assert not at.exception, at.exception
    assert store.state(cid).state.value == "NARRATIVE"

    runner.run_case_steps()  # template draft
    at = run("views/quality_check.py", role="safety_specialist", case_id=cid)
    at.text_input[-1].input("demo")
    next(b for b in at.button if b.label == "Sign narrative").click().run()
    assert not at.exception, at.exception
    assert any("[brackets]" in e.value for e in at.error)  # the template note is still there
    assert store.state(cid).state.value == "NARRATIVE_REVIEW"

    box = next(a for a in at.text_area if a.label == "Narrative")
    box.input(box.value.split("[Add")[0] + "Chest pain on admission.")
    at.text_input[-1].input("demo")
    next(b for b in at.button if b.label == "Sign narrative").click().run()
    assert not at.exception, at.exception
    assert store.state(cid).state.value == "READY_FOR_ADJUDICATION"
    assert 'aria-current="step">Ready for adjudication' in html_text(at)


def test_quality_check_page_shows_ai_proposals(demo, monkeypatch, tmp_path):
    import json

    from adjud.documents import upload
    from adjud.review import submit
    from adjud.runner import Runner
    from tools import make_flow2_fixtures

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    fixtures = tmp_path / "flow2"
    make_flow2_fixtures.make(fixtures)
    runner = Runner(demo)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for(json.loads((fixtures / "expected.json").read_text())["case"])
    files = sorted((fixtures / "different_event").iterdir())
    upload(store, cid, [(f.name, f.read_bytes()) for f in files], "site101.coord")
    runner.run_case_steps()
    submit(store, cid, "site101.coord", "demo", demo.config)
    for _ in range(2):
        runner.run_case_steps()
    at = run("views/quality_check.py", role="safety_specialist", case_id=cid)
    table = next(df.value for df in at.dataframe if "Source" in df.value.columns)
    assert table.iloc[0]["Source"] == "AI" and table.iloc[0]["Accepted"] == "Yes"
    assert any("fracture" in i.value for i in at.info)  # the query the agent sent


def test_a_site_sees_only_its_own_subjects_on_every_page(demo):
    """Site segregation (demo checklist): the case list, the case record overview and the
    import results show a site user only subjects of their own site."""
    from adjud.runner import Runner

    runner = Runner(demo)
    runner.run_once()  # imports 01_initial.csv: cases at sites 101, 102 and 104
    states = runner.pipeline.store.list_case_states()
    own = {cs.case_id for cs in states if cs.subject.startswith("DEMO01-101-")}
    assert own and len(own) < len(states)
    site101 = {"id": "site101.coord", "name": "Site 101 Coordinator", "role": "site",
               "site": "101", "email": "site101@example.com"}  # fmt: skip
    for page in (None, "views/results.py"):
        at = run(page, role="site", user_site=site101)
        shown = [df.value for df in at.dataframe]
        subjects = {s for df in shown if "Subject" in df for s in df["Subject"]}
        assert subjects and all(s.startswith("DEMO01-101-") for s in subjects), subjects
        listed = {o.split(",")[0] for o in at.selectbox(key="case_id").options}
        assert listed == own, listed
    at = run(role="data_manager")
    subjects = {s for df in at.dataframe if "Subject" in df.value for s in df.value["Subject"]}
    assert {s.split("-")[1] for s in subjects} >= {"101", "102"}


def test_a_refresh_comes_back_to_the_chosen_case(demo):
    """The sidebar writes role, user and case into the URL; a new session (a browser
    refresh) reads them back instead of falling back to "Choose a case"."""
    from adjud.runner import Runner

    Runner(demo).run_once()
    at = run("views/site_documents.py", role="site")
    next(s for s in at.selectbox if s.label == "Case").select("DEMO01-0001").run()
    assert not at.exception, at.exception
    url = {k: at.query_params[k] for k in ("role", "user", "case")}
    assert url == {"role": "site", "user": "site101.coord", "case": "DEMO01-0001"}

    fresh = AppTest.from_file(APP, default_timeout=30)
    for k, v in url.items():
        fresh.query_params[k] = v
    fresh.run()
    assert not fresh.exception, fresh.exception
    assert fresh.session_state["case_id"] == "DEMO01-0001"
    assert fresh.session_state["user_site"]["id"] == "site101.coord"


def test_typed_black_out_is_offered_only_for_spreadsheets(demo):
    """PDF and scan pages take boxes; a spreadsheet is a table, so it keeps typed black-out."""
    from adjud.documents import upload
    from adjud.runner import Runner

    runner = Runner(demo)
    runner.run_once()
    src = demo.root / "demo_uploads" / "documents" / "DEMO01-101-0001_AE-001"
    files = [(f.name, f.read_bytes()) for f in sorted(src.iterdir())]
    upload(runner.pipeline.store, "DEMO01-0001", files, "site101.coord")
    runner.run_once()

    at = run("views/site_documents.py", role="site", case_id="DEMO01-0001")
    pick = next(s for s in at.selectbox if s.label == "File to review")
    assert pick.value.endswith(".pdf")
    assert not any(b.label == "Black out this text" for b in at.button)
    pick.select("troponin_results.xlsx").run()
    assert not at.exception, at.exception
    assert any(b.label == "Black out this text" for b in at.button)
