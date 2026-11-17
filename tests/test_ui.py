"""Headless smoke tests of the Streamlit pages (streamlit.testing AppTest)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adjud.paths import DataPaths
from tools.reset_demo import reset

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "ui" / "app.py")
KIT_SCEN = ROOT / "data" / "flow1-testkit" / "inbox_scenarios"


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
    at = run("views/edc_input.py", role="data_manager")
    assert "Synthetic data only." in html_text(at)
    import_now = next(b for b in at.button if b.label == "Import queued files")
    import_now.click().run()
    assert not at.exception, at.exception
    assert any(
        "Imported 01_initial.csv: 22 records, 11 new cases, 11 not eligible." in s.value
        for s in at.success
    )
    assert len([p for p in demo.cases.iterdir() if p.is_dir()]) == 11
    # The page redraws: the sidebar already lists the new cases, the queue note is gone.
    assert len(next(s for s in at.selectbox if s.label == "Case").options) == 11
    assert not any("waiting in the import queue" in m.value for m in at.markdown)


@pytest.mark.parametrize(
    ("role", "own"),
    [
        ("data_manager", ["assistant", "edc_input", "results"]),
        ("site", ["assistant", "site_documents", "results"]),
        ("safety_specialist", ["assistant", "quality_check", "results"]),
        ("cec_coordinator", ["assistant", "assignments", "committee", "results"]),
        ("adjudicator", ["assistant", "adjudication", "committee", "results"]),
    ],
)
def test_each_role_sees_only_its_own_pages(demo, role, own):
    """The menu lists only the role's pages, it lands on its own first page (the
    Assistant, P-40), and another role's page (a typed URL) sends it back there."""
    titles = {"assistant": "Assistant", "edc_input": "AE import", "site_documents": "Documents",
              "quality_check": "Quality check", "results": "Case record",
              "assignments": "Assignments", "adjudication": "Adjudication",
              "committee": "Committee"}  # fmt: skip
    at = run(role=role)
    assert titles[own[0]] in [t.value for t in at.title]
    assert [link.proto.label for link in at.get("page_link")] == [titles[p] for p in own]
    for page in titles:
        at.switch_page(f"views/{page}.py")
        at.run()
        assert not at.exception, at.exception
        landed = titles[page] if page in own else titles[own[0]]
        assert landed in [t.value for t in at.title], (page, landed)


def test_results_and_documents_pages_render_for_a_case(demo):
    from adjud.runner import Runner

    Runner(demo).run_once()
    case_id = sorted(p.name for p in demo.cases.iterdir() if p.is_dir())[0]
    at = run("views/results.py", role="safety_specialist", case_id=case_id)
    assert "Intact" in html_text(at) and "Case " + case_id in html_text(at)
    site101 = {"id": "site101.coord", "name": "Priya Nair", "role": "site",
               "site": "101", "email": "priya.nair@larkfield-general.example"}  # fmt: skip
    run("views/site_documents.py", role="site", user_site=site101, case_id=case_id)


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
    assert any("Signed by Priya Nair" in m.value for m in at.markdown)


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


def test_quality_check_page_is_one_stop_with_the_narrative_and_one_signature(demo):
    """P-39 with AI off: the narrative draft (the template) is on the quality check page
    from the start, and one signature passes QC and signs the narrative as edited."""
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
    for _ in range(3):  # handoff, QC start, QC round (dossier and draft included)
        runner.run_case_steps()

    at = run("views/quality_check.py", role="safety_specialist", case_id=cid)
    assert 'aria-current="step">Quality check' in html_text(at)
    page = html_text(at) + " ".join(m.value for m in at.markdown)
    assert "No AI Suggestion this round" in page and "Suggested outcome" in page
    doc = next(s for s in at.selectbox if s.label == "Document")  # the submitted files, read-only
    assert doc.options == ["ecg.pdf", "summary.pdf"] and at.get("image")
    assert any(b.label == "Download the dossier (PDF)" for b in at.get("download_button"))
    # Query fields only once the Safety Specialist chooses to send a query.
    decision = next(r for r in at.radio if r.label == "Your decision")
    assert decision.value == "pass" and not at.multiselect
    assert any(a.label == "Narrative" for a in at.text_area)  # the draft, ready to edit
    assert not any(a.label == "Message to the site" for a in at.text_area)
    next(a for a in at.text_area if a.label == "Narrative").input("My edit. [Add").run()
    decision.set_value("query").run()
    assert [m.label for m in at.multiselect] == ["Query reasons"]
    assert any(a.label == "Message to the site" for a in at.text_area)
    next(r for r in at.radio if r.label == "Your decision").set_value("pass").run()
    assert not at.multiselect
    box = next(a for a in at.text_area if a.label == "Narrative")
    assert box.value == "My edit. [Add"  # the edit survived the switch to query and back
    box.input(store.get_file(cid, "narrative/draft_v1.md").decode()).run()
    sign = "Sign the pass and the narrative"
    at.text_input[-1].input("demo")
    next(b for b in at.button if b.label == sign).click().run()
    assert not at.exception, at.exception
    assert any("[brackets]" in e.value for e in at.error)  # the template note is still there
    assert store.state(cid).state.value == "QC_REVIEW"

    box = next(a for a in at.text_area if a.label == "Narrative")
    box.input(box.value.split("[Add")[0] + "Chest pain on admission.")
    at.text_input[-1].input("demo")
    next(b for b in at.button if b.label == sign).click().run()
    assert not at.exception, at.exception
    assert store.state(cid).state.value == "READY_FOR_ADJUDICATION"
    assert 'aria-current="step">Ready for adjudication' in html_text(at)
    assert any("narrative signed by Dr. Anna Weber" in s.value for s in at.success)

    runner.run_case_steps()  # Flow 3 handoff
    at = run("views/results.py", role="safety_specialist", case_id=cid)
    assert not at.exception, at.exception
    assert "Added: " in html_text(at) and "Chest pain on admission." in html_text(at)


def test_quality_check_page_shows_the_ai_suggestion(demo, monkeypatch, tmp_path):
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
    assert not at.exception, at.exception
    page = html_text(at)
    onset = page[page.index("<b>Onset date</b>") :].split("</tr>")[0]
    assert "2026-03-14</td>" in onset and "Doesn&#x27;t match" in onset
    assert "discharge_summary_nov2025.pdf p. 1" in onset  # the quote it rests on
    # The AI suggests; the query is pre-filled and a person sends it (P-37, ADR 0001).
    assert "send a query to the site" in html_text(at)
    assert next(r for r in at.radio if r.label == "Your decision").value == "query"
    message = next(a for a in at.text_area if a.label.startswith("Message to the site"))
    assert "Onset date: EDC 2026-03-14, documents 02/11/2025." in message.value
    assert ".." not in message.value
    assert not at.info  # nothing has been sent to the site yet
    next(r for r in at.radio if r.label == "Your decision").set_value("pass").run()
    assert any(t.label.startswith("Why you pass despite") for t in at.text_input)


def test_a_site_sees_only_its_own_subjects_on_every_page(demo):
    """Site segregation (demo checklist): the case list, the case record overview and the
    import results show a site user only subjects of their own site."""
    from adjud.runner import Runner

    runner = Runner(demo)
    runner.run_once()  # imports 01_initial.csv: cases at sites 101, 102 and 104
    states = runner.pipeline.store.list_case_states()
    own = {cs.case_id for cs in states if cs.subject.startswith("DEMO01-101-")}
    assert own and len(own) < len(states)
    site101 = {"id": "site101.coord", "name": "Priya Nair", "role": "site",
               "site": "101", "email": "priya.nair@larkfield-general.example"}  # fmt: skip
    for page in ("views/site_documents.py", "views/results.py"):
        at = run(page, role="site", user_site=site101)
        shown = [df.value for df in at.dataframe]
        subjects = {s for df in shown if "Subject" in df for s in df["Subject"]}
        assert subjects and all(s.startswith("DEMO01-101-") for s in subjects), subjects
        picker = next(s for s in at.selectbox if s.label == "Case")
        listed = {o.split(",")[0] for o in picker.options}
        assert listed == own, listed
    at = run("views/edc_input.py", role="data_manager")
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


def test_manual_ae_form_uses_pickers_and_saves_a_case(demo):
    """Readable labels, pickers for coded fields, date pickers; every problem at once."""
    import datetime as dt

    at = run("views/edc_input.py", role="data_manager")
    next(b for b in at.button if b.label == "Save AE").click().run()
    error = at.error[0].value
    assert "Why it is entered by hand: required" in error and "Subject ID: required" in error

    def text(label, value):
        next(t for t in at.text_input if t.label.startswith(label)).input(value)

    def pick(label, value):
        next(s for s in at.selectbox if s.label.startswith(label)).select(value)

    text("Subject ID", "DEMO01-104-0012")
    text("Site", "104")
    text("AE ID", "AE-M01")
    text("AE term", "Ischaemic stroke")
    text("Preferred term code", "90000201")
    next(d for d in at.date_input if d.label.startswith("Onset date")).set_value(
        dt.date(2026, 8, 20)
    )
    pick("Serious", "Y")
    pick("Seriousness criterion", "HOSPITALISATION")
    pick("Outcome", "RECOVERING")
    next(a for a in at.text_area if a.label.startswith("Why it is entered")).input("By phone")
    next(b for b in at.button if b.label == "Save AE").click().run()
    assert not at.exception, at.exception
    assert any(s.value.startswith("Saved: new case") for s in at.success), at.error
    assert len(next(s for s in at.selectbox if s.label == "Case").options) == 1


def test_cases_to_check_names_the_case_a_duplicate_may_repeat(demo):
    at = run("views/edc_input.py", role="data_manager")
    next(b for b in at.button if b.label == "Import queued files").click().run()
    shutil.copy(KIT_SCEN / "07_duplicate_candidate.csv", demo.inbox)
    at.run()  # the button shows only while a file is queued
    next(b for b in at.button if b.label == "Import queued files").click().run()
    assert not at.exception, at.exception
    flagged = at.dataframe[-1].value
    assert list(flagged["Why"]) == ["Possible duplicate of DEMO01-0011"]


def test_the_next_step_links_to_the_page_where_this_person_acts(demo):
    """The case header links to where the viewer's next action is, never to the page
    already open, and not at all for someone with nothing to do."""
    from adjud.runner import Runner

    Runner(demo).run_once()  # cases wait for documents

    def step_links(at):  # the sidebar menu's links are not next steps
        menu = {"Assistant", "AE import", "Documents", "Quality check", "Case record"}
        return [p.proto.label for p in at.get("page_link") if p.proto.label not in menu]

    at = run("views/results.py", role="site", case_id="DEMO01-0001")
    assert step_links(at) == ["Upload documents"]
    at = run("views/site_documents.py", role="site", case_id="DEMO01-0001")
    assert not step_links(at)  # already on Documents
    at = run("views/results.py", role="safety_specialist", case_id="DEMO01-0001")
    assert not step_links(at)  # nothing for the Safety Specialist to do yet


def test_selecting_an_import_result_shows_why_below_the_table(demo):
    at = run("views/edc_input.py", role="data_manager")
    next(b for b in at.button if b.label == "Import queued files").click().run()
    assert any("Select a row to see why" in h for h in html_text(at).splitlines())
    table = next(df for df in at.dataframe if "Result" in df.value.columns)
    row = int(table.value.index[table.value["AE"] == "AE-008"][0])
    at.session_state["outcomes_01_initial.csv.report.json"] = {
        "selection": {"rows": [row], "columns": [], "cells": []}
    }
    at.run()
    assert not at.exception, at.exception
    assert any(s.value == "Why: DEMO01-101-0007, AE-008" for s in at.subheader)
    assert "This record does not meet the study's rules" in html_text(at)
    assert "Not met</span>: Serious adverse event" in html_text(at)


def test_a_pop_up_shows_each_email_sent_about_a_case_the_viewer_may_see(demo):
    """E-mails the runner sends pop up at the bottom of the screen; a site user sees those
    about its own cases only, and nothing already sent before the page was opened."""
    import json

    from adjud.runner import Runner

    Runner(demo).run_once()  # three sites' "documents requested" notifications, not e-mailed
    notes = {n["case_id"]: n for n in (json.loads(f.read_text())
             for f in demo.outbox.glob("*.json"))}  # fmt: skip
    site101 = {"id": "site101.coord", "name": "Priya Nair", "role": "site",
               "site": "101", "email": "priya.nair@larkfield-general.example"}  # fmt: skip
    log = demo.outbox / "delivery.jsonl"
    earlier = {"id": notes["DEMO01-0001"]["id"], "status": "sent", "to": ["site101.coord"],
               "at": "2026-11-16T00:00:00Z"}  # fmt: skip
    log.write_text(json.dumps(earlier) + "\n")
    at = run(role="site", user_site=site101)
    assert not at.toast  # sent before the page was opened

    with log.open("a") as f:
        for cid, status in (("DEMO01-0006", "sent"), ("DEMO01-0003", "sent"),
                            ("DEMO01-0008", "failed")):  # fmt: skip
            note = notes[cid]
            f.write(json.dumps({"id": note["id"], "status": status, "to": ["x"],
                                "at": "2026-11-16T00:00:10Z"}) + "\n")  # fmt: skip
    at.run()
    assert not at.exception, at.exception
    shown = [t.value for t in at.toast]
    sites = {cid: notes[cid]["to"] for cid in ("DEMO01-0006", "DEMO01-0003", "DEMO01-0008")}
    assert sites["DEMO01-0006"] == "site:101" and sites["DEMO01-0003"] == "site:102"
    assert any(t.startswith("DEMO01-0006: e-mail sent to") for t in shown)
    assert not any("DEMO01-0003" in t for t in shown)  # site 102's case
    assert any(t.startswith("DEMO01-0008: e-mail to") and "failed" in t for t in shown)


def test_switching_role_moves_to_that_roles_page_and_keeps_the_role(demo):
    """Switching role keeps the new role (the page switch once dropped it and snapped
    back), lands on the Assistant, and the menu offers that role's own pages."""
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["role"] = "data_manager"
    at.run()
    assert "Assistant" in [t.value for t in at.title]
    for role, page in (("site", "Documents"), ("safety_specialist", "Quality check"),
                       ("data_manager", "AE import")):  # fmt: skip
        next(s for s in at.selectbox if s.label == "Role").select(role).run()
        assert not at.exception, at.exception
        assert at.session_state["role"] == role and at.query_params["role"] == role
        assert "Assistant" in [t.value for t in at.title]
        assert page in [link.proto.label for link in at.get("page_link")]


def test_the_site_sees_recognised_document_types_and_can_correct_one(demo):
    from adjud.documents import documents_of, upload
    from adjud.runner import Runner

    runner = Runner(demo)
    runner.run_once()
    src = demo.root / "demo_uploads" / "documents" / "DEMO01-101-0001_AE-001"
    files = [(f.name, f.read_bytes()) for f in sorted(src.iterdir())]
    upload(runner.pipeline.store, "DEMO01-0001", files, "site101.coord")
    runner.run_once()

    at = run("views/site_documents.py", role="site", case_id="DEMO01-0001")
    table = next(df.value for df in at.dataframe if "Type" in df.value.columns)
    assert sorted(table["Type"]) == ["Discharge or death summary", "ECG report",
                                     "Laboratory results"]  # fmt: skip
    kind = next(s for s in at.selectbox if s.label == "Document type")
    assert kind.value == "discharge_summary" and not kind.disabled
    kind.select("other").run()
    assert not at.exception, at.exception
    docs = documents_of(runner.pipeline.store, "DEMO01-0001")
    assert docs.type_of("discharge_summary.pdf") == ("other", "site")
