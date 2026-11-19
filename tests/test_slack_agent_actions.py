"""The Slack Agent's unsigned steps (plan P-58, Q4, Q8, Q13): the data manager's import
and pull, a site's upload of attached files, the Safety Specialist's narrative and Site
Query drafts that pre-fill the Quality check page, the Site Query in the site's DM, and
every refusal (wrong role, wrong site, wrong state, an external, foreign or oversized
file). No signature and no Quality Check decision ever comes from Slack.

A fake Slack client and a fake download stand in for Slack, and the Agent runs on
replay with answers written in the test's folder: nothing reaches Slack or the API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from slack_sdk.errors import SlackApiError
from streamlit.testing.v1 import AppTest

from adjud.ai.tools import ToolError
from adjud.delivery import DeliveryLog, directory, people
from adjud.documents import safe_filename, upload
from adjud.events import EventDraft
from adjud.flow2 import Flow2, qc_drafts
from adjud.integrity import verify_all
from adjud.runner import Runner
from adjud.slack_app.agent import NO_FILES
from adjud.slack_app.agent_tools import AgentTools, Turn, agent_toolbox
from adjud.slack_app.conversations import ConversationLog
from adjud.slack_app.core import Directory, SlackActionsLog, Workspace
from adjud.slack_app.dm import NARRATIVE_HELD, NoticeDetail, SlackDmChannel, SlackSettings
from adjud.slack_app.files import SlackFiles
from adjud.slack_app.phi_gate import HOLD_TEXT, PhiGate
from adjud.workflow import State
from tests import test_slack_agent_tools as with_cases
from tests.test_flow3 import pdf, to_qc_review
from tests.test_slack_agent import DM, ME, FakeSlack, answer, log_of, message, replay_agent

ROOT = Path(__file__).resolve().parents[1]
data, ws = with_cases.data, with_cases.ws  # fixtures: the demo's three cases, imported
CID = "DEMO01-0001"  # site 101 (Priya); DEMO01-0002 is site 102's (Tom)


class FilesSlack(FakeSlack):
    """Also `token`, `files_info` and `files_delete`, which refuses a person's file as
    Slack does for a bot."""

    token = "xoxb-test"

    def __init__(self, *a, delete_error: str | None = "cant_delete_file", **kw):
        super().__init__(*a, **kw)
        self.deleted: list[str] = []
        self.delete_error = delete_error

    def files_delete(self, *, file):
        self.deleted.append(file)
        if self.delete_error:
            raise SlackApiError("refused", {"ok": False, "error": self.delete_error})

    def files_info(self, *, file):
        raise AssertionError("full file objects are given")


class Downloads:
    """Slack's file host: url -> bytes; remembers every request and its headers."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.requests = files, []

    def __call__(self, url: str, headers: dict[str, str], limit: int) -> bytes:
        self.requests.append((url, headers))
        return self.files[url][:limit]


def attached(fid: str, name: str, content: bytes, **kw) -> dict:
    return {"id": fid, "name": name, "mode": "hosted", "is_external": False,
            "size": len(content),
            "url_private_download": f"https://files.slack.com/files-pri/T0-{fid}/{name}",
            **kw}  # fmt: skip


def acting(ws: Workspace, person_id: str, files: list[dict], downloads: Downloads,
           client: FilesSlack | None = None) -> AgentTools:  # fmt: skip
    turn = Turn(ME, SlackActionsLog(ws.paths), files,
                SlackFiles(client or FilesSlack(), opener=downloads))  # fmt: skip
    return AgentTools(ws, ws.people.by_id[person_id], turn)


def attached_as(ws: Workspace, person_id: str, thread: str, file_ids: list[str]) -> None:
    """The record of an earlier turn whose message carried these files."""
    ConversationLog(ws.paths).record(slack_user=ME, acting_as=person_id, channel=DM,
                                     thread=thread, kind="turn", outcome="posted",
                                     attachments=len(file_ids), attached=file_ids)  # fmt: skip


def call(tools: AgentTools, name: str, **args) -> dict:
    return json.loads(tools.toolbox().call(name, args))


def actions_of(ws: Workspace) -> list[dict]:
    return SlackActionsLog(ws.paths).log.records()


def uploads(ws: Workspace, cid: str) -> list[dict]:
    return [e for e in ws.store.events(cid) if e["type"] == "documents.uploaded"]


# -- the data manager: import and pull ------------------------------------------------------


def test_the_data_manager_imports_through_the_agent_as_themselves(data, tmp_path):
    w = Workspace(data)  # 01_initial.csv waits in the import queue
    w.people.act_as(ME, "dm.dana")
    agent = replay_agent(w, tmp_path)
    answer(agent, "dm.dana", "Import the waiting files", "Imported 1 file: 3 new cases.",
           [{"tool": "import_waiting_files", "input": {}}])  # fmt: skip
    agent.on_message(message("Import the waiting files"), FakeSlack())
    assert len(w.store.list_cases()) == 3 and not w.waiting_files()
    created = [e for c in w.store.list_cases() for e in w.store.events(c)
               if e["type"] == "case.created"]  # fmt: skip
    assert {e["actor"] for e in created} == {"dm.dana"}  # as that person, as from the app
    (done,) = [a for a in actions_of(w) if a["action"] == "agent:import"]
    assert (done["slack_user"], done["acting_as"], len(done["cases"])) == (ME, "dm.dana", 3)
    assert log_of(w.paths)[-1]["done"] == [{"action": "import", "result": "done",
                                            "cases": done["cases"]}]  # fmt: skip

    pulled = call(acting(w, "dm.dana", [], Downloads({})), "pull_from_edc")
    assert pulled["done"].startswith("Pulled the AE records from the EDC.")
    assert actions_of(w)[-1]["action"] == "agent:pull"


def test_acting_tools_belong_to_their_role_only(ws):
    for person, tool in (("site101.coord", "import_waiting_files"),
                         ("ss.anna", "upload_attached_files"),
                         ("dm.dana", "edit_narrative_draft"),
                         ("site101.coord", "draft_site_query"),
                         ("adj.okafor", "upload_attached_files")):  # fmt: skip
        with pytest.raises(ToolError, match="unknown tool"):
            agent_toolbox(ws, ws.people.by_id[person]).call(tool, {"case": CID})


# -- a site's upload from Slack (Q8) ------------------------------------------------------------


def test_a_site_adds_the_files_attached_to_its_message_to_its_case(ws, tmp_path):
    ws.people.act_as(ME, "site101.coord")
    summary, ecg = pdf("DISCHARGE SUMMARY\nChest pain."), pdf("ECG REPORT\nST elevation.")
    files = [attached("F1", "summary.pdf", summary), attached("F2", "ecg.pdf", ecg)]
    downloads = Downloads({f["url_private_download"]: c for f, c in zip(files, (summary, ecg),
                                                                       strict=True)})  # fmt: skip
    agent = replay_agent(ws, tmp_path)
    agent.opener = downloads
    answer(agent, "site101.coord", "Here they are for 0001\n\n(attached 2 files)",
           "Added summary.pdf and ecg.pdf to DEMO01-0001. Identifiers are being removed.",
           [{"tool": "upload_attached_files", "input": {"case": "0001"}}])  # fmt: skip
    client = FilesSlack()
    agent.on_message(message("Here they are for 0001", subtype="file_share", files=files),
                     client)  # fmt: skip

    assert ws.store.state(CID).state == State.DEIDENTIFYING  # the runner goes on as today
    (up,) = uploads(ws, CID)
    assert up["actor"] == "site101.coord" and up["data"]["via"] == {"slack_files": ["F1", "F2"]}
    assert [f["name"] for f in up["data"]["files"]] == ["summary.pdf", "ecg.pdf"]
    assert ws.store.get_file(CID, "documents/original/ecg.pdf") == ecg
    # the token went to Slack's file host only
    assert all(u.startswith("https://files.slack.com/") and h == {"Authorization":
               "Bearer xoxb-test"} for u, h in downloads.requests)  # fmt: skip
    # delete was tried; Slack refused, so the original stays in Slack, and that is recorded
    assert client.deleted == ["F1", "F2"]
    (done,) = [a for a in actions_of(ws) if a["action"] == "agent:upload"]
    assert (done["acting_as"], done["cases"], done["result"]) == ("site101.coord", [CID], "done")
    assert done["detail"] == {"files": 2, "refused": 0, "deleted_from_slack": 0,
                              "slack_said": ["cant_delete_file"]}  # fmt: skip
    entry = log_of(ws.paths)[-1]
    assert (entry["outcome"], entry["attachments"], entry["case_id"]) == ("posted", 2, CID)
    assert entry["done"][0]["action"] == "upload"
    assert client.texts[-1].startswith("Added summary.pdf")
    url = client.posts[-1]["blocks"][-2]["elements"][0]["url"]
    assert "/documents?" in url and "user=site101.coord" in url and f"case={CID}" in url
    assert all(r.ok for r in verify_all(ws.paths))

    # the same Slack file again is not added twice
    with pytest.raises(ToolError, match="already in the case"):
        call(acting(ws, "site101.coord", files[:1], downloads), "upload_attached_files",
             case=CID)  # fmt: skip
    assert len(uploads(ws, CID)) == 1


def test_a_file_attached_earlier_in_the_thread_is_added_and_said_even_if_the_answer_fails(
    ws, tmp_path
):
    """The site attaches a file, is asked which case, and names it: the file of the earlier
    message is added. The recorded answer then turns out differently (as a live error
    after the tool would): code still says what was added, never only that it failed."""
    ws.people.act_as(ME, "site101.coord")
    ecg = pdf("ECG REPORT\nST elevation.")
    files = [attached("F3", "ecg.pdf", ecg)]
    agent = replay_agent(ws, tmp_path)
    agent.opener = Downloads({files[0]["url_private_download"]: ecg})
    first = "1700000100.000100"
    thread = [
        {"user": ME, "text": "", "files": files, "subtype": "file_share", "ts": first},
        {"bot_id": "B0APP", "text": "Which case is it for?", "ts": "1700000101.0"},
    ]
    attached_as(ws, "site101.coord", first, ["F3"])  # the first message's turn
    answer(agent, "site101.coord", ["(attached 1 file)", "Which case is it for?", "0001"],
           "Added ecg.pdf to DEMO01-0001.",
           [{"tool": "upload_attached_files", "input": {"case": "0001"},
             "error": True}])  # fmt: skip
    client = FilesSlack(thread)
    agent.on_message(message("0001", ts="1700000102.0", thread_ts=first), client)
    (up,) = uploads(ws, CID)
    assert up["data"]["via"] == {"slack_files": ["F3"]}
    assert client.texts == ["Added 1 file to DEMO01-0001. Identifiers are being removed now; "
                            "review and sign the redacted pages in the app."]  # fmt: skip
    entry = log_of(ws.paths)[-1]
    assert (entry["source"], entry["outcome"], entry["done"][0]["files"]) == ("code", "posted", 1)


def test_an_upload_is_refused_for_another_site_a_closed_case_or_a_file_it_cannot_take(ws):
    content = pdf("DISCHARGE SUMMARY\nChest pain.")
    good = attached("F1", "summary.pdf", content)
    downloads = Downloads(
        {
            good["url_private_download"]: content,
            "https://files.slack.com/F9/page.pdf": b"<!DOCTYPE html><html>",
        }
    )

    def refused(person: str, case: str, files: list[dict], why: str) -> None:
        with pytest.raises(ToolError, match=why):
            call(acting(ws, person, files, downloads), "upload_attached_files", case=case)

    refused("site102.coord", CID, [good], "no case")  # another site's case: as no such case
    refused("site101.coord", CID, [], "no file is attached")
    refused("site101.coord", CID, [{**good, "is_external": True}], "a link to a file elsewhere")
    refused("site101.coord", CID, [{**good, "mode": "external"}], "a link to a file elsewhere")
    for url in ("https://evil.example.com/summary.pdf", "http://files.slack.com/F1/a.pdf",
                "https://files.slack.com.evil.example/F1/a.pdf"):  # fmt: skip
        refused("site101.coord", CID, [{**good, "url_private_download": url}], "no download")
    refused("site101.coord", CID, [{**good, "size": 51 * 1024 * 1024}], "larger than")
    refused("site101.coord", CID, [{**good, "name": "notes.docx"}], "not accepted")
    assert downloads.requests == []  # all of these refused before any request
    sign_in = attached("F9", "page.pdf", b"x" * 10,
                       url_private_download="https://files.slack.com/F9/page.pdf")  # fmt: skip
    refused("site101.coord", CID, [sign_in], "damaged")  # Slack's HTML page, not a PDF
    assert uploads(ws, CID) == []

    ws.store.append_event(CID, EventDraft(type="case.withdrawn", actor="dm.dana", data={}))
    refused("site101.coord", CID, [good], "does not take documents now")
    assert uploads(ws, CID) == [] and ws.store.state(CID).state == State.WITHDRAWN


def test_without_the_agents_ai_attached_files_are_not_added_and_the_reply_says_so(ws):
    from adjud.slack_app.agent import SlackAgent
    from adjud.slack_app.conversations import ConversationLog

    ws.people.act_as(ME, "site101.coord")
    content = pdf("ECG REPORT")
    files = [attached("F1", "ecg.pdf", content)]
    agent = SlackAgent(ws, ConversationLog(ws.paths))  # study.yaml: the Agent's AI is off
    agent.opener = Downloads({files[0]["url_private_download"]: content})
    client = FilesSlack()
    agent.on_message(message("", subtype="file_share", files=files), client)
    assert client.texts == [NO_FILES] and uploads(ws, CID) == []
    url = client.posts[0]["blocks"][-2]["elements"][0]["url"]
    assert "/documents?" in url


def test_a_file_named_after_the_patient_is_added_but_a_reply_naming_it_is_held(ws, tmp_path):
    ws.people.act_as(ME, "site101.coord")
    gate = PhiGate.for_workspace(ws)
    who = gate.identities[ws.store.state(CID).subject]
    name = f"{who['last']}_{who['first']}_discharge.pdf"
    content = pdf("DISCHARGE SUMMARY")
    files = [attached("F1", name, content)]
    agent = replay_agent(ws, tmp_path)
    agent.opener = Downloads({files[0]["url_private_download"]: content})
    answer(agent, "site101.coord", "For 0001\n\n(attached 1 file)",
           f"Added {safe_filename(name)} to DEMO01-0001.",
           [{"tool": "upload_attached_files", "input": {"case": "0001"}}])  # fmt: skip
    client = FilesSlack()
    agent.on_message(message("For 0001", subtype="file_share", files=files), client)
    assert len(uploads(ws, CID)) == 1  # the upload stands
    assert client.texts == [HOLD_TEXT]  # the reply naming it does not
    entry = log_of(ws.paths)[-1]
    assert entry["outcome"] == "held" and "reply" not in entry and entry["done"]
    assert who["last"] not in json.dumps(log_of(ws.paths))


# -- the Safety Specialist's drafts and the Site Query (Q4, Q13) -----------------------------------


@pytest.fixture
def in_qc_review(ws) -> Runner:
    runner = Runner(ws.paths)
    upload(ws.store, CID, [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain.")),
                           ("ecg.pdf", pdf("ECG REPORT\nST elevation."))],
           "site101.coord")  # fmt: skip
    to_qc_review(runner, ws.store, CID)
    return runner


def test_drafts_from_slack_never_move_the_case_nor_sign(ws, in_qc_review, tmp_path):
    ws.people.act_as(ME, "ss.anna")
    agent = replay_agent(ws, tmp_path)
    edited = "The patient had chest pain and was treated with a stent. Discharged home."
    answer(agent, "ss.anna", "Change the narrative of 0001", "Saved the narrative draft, "
           "unsigned. Sign it in the app.",
           [{"tool": "edit_narrative_draft",
             "input": {"case": "0001", "text": edited}}])  # fmt: skip
    before = len(ws.store.events(CID))
    client = FakeSlack()
    agent.on_message(message("Change the narrative of 0001"), client)
    tools = acting(ws, "ss.anna", [], Downloads({}))
    out = call(tools, "draft_site_query", case="0001", reasons=["missing_document"],
               text="Please send the ECG report.")  # fmt: skip
    assert out["saved"].startswith("Site Query draft, unsigned")

    new = ws.store.events(CID)[before:]
    assert [e["type"] for e in new] == ["narrative.draft_edited", "query.drafted"]
    assert all(e["actor"] == "ss.anna" and not e.get("signature") for e in new)
    assert ws.store.state(CID).state == State.QC_REVIEW  # no transition from Slack
    drafts = qc_drafts(ws.store, CID)  # what the Quality check page starts from
    assert drafts.narrative.strip() == edited
    assert (drafts.query_codes, drafts.query_text) == (["missing_document"],
                                                       "Please send the ECG report.")  # fmt: skip
    assert call(tools, "narrative_draft", case="0001")["edited_unsigned"] is True
    assert all(r.ok for r in verify_all(ws.paths))  # the edit's file is referenced by hash
    url = client.posts[-1]["blocks"][-2]["elements"][0]["url"]  # sign it there
    assert "/qc?" in url and "user=ss.anna" in url and f"case={CID}" in url

    with pytest.raises(ToolError, match="unknown query reasons"):
        call(tools, "draft_site_query", case="0001", reasons=["more_data"], text="x")
    with pytest.raises(ToolError, match="not waiting for a QC review"):
        call(tools, "edit_narrative_draft", case="DEMO01-0003", text=edited)
    with pytest.raises(ToolError, match="not waiting for a QC review"):
        call(tools, "draft_site_query", case="DEMO01-0003", reasons=["other"], text="x")


def test_the_quality_check_page_starts_from_the_drafts(ws, in_qc_review):
    flow2 = Flow2(ws.store, ws.paths.config)
    flow2.save_narrative_draft(CID, "ss.anna", "Edited in Slack.", via="slack")
    flow2.save_query_draft(CID, "ss.anna", ["missing_document"], "Send the ECG.", via="slack")
    at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=30)
    at.session_state["role"], at.session_state["case_id"] = "safety_specialist", CID
    at.run()
    at.switch_page("views/quality_check.py")
    at.run()
    assert not at.exception, at.exception
    decision = next(r for r in at.radio if r.label == "Your decision")
    assert decision.value == "query"  # the latest draft is the query
    assert at.multiselect[0].value == ["missing_document"]
    assert next(a for a in at.text_area if a.label == "Message to the site").value == (
        "Send the ECG."
    )
    decision.set_value("pass").run()
    assert next(a for a in at.text_area if a.label == "Narrative").value == "Edited in Slack.\n"
    assert ws.store.state(CID).state == State.QC_REVIEW


def test_the_signed_query_reaches_the_sites_dm_and_is_answered_by_upload(ws, in_qc_review,
                                                                         tmp_path):  # fmt: skip
    flow2 = Flow2(ws.store, ws.paths.config)
    flow2.save_query_draft(CID, "ss.anna", ["missing_document"], "Please send the ECG report.")
    draft = qc_drafts(ws.store, CID)
    flow2.open_query(CID, draft.query_codes, draft.query_text, user_id="ss.anna",
                     password="demo")  # signed in the app (ADR 0001)  # fmt: skip
    assert ws.store.state(CID).state == State.QUERY_OPEN
    note = next(n for n in DeliveryLog(ws.store).notes() if n["template"] == "query_opened")

    ws.people.act_as(ME, "site101.coord")
    client = FilesSlack()
    channel = SlackDmChannel(Directory(ws.paths.config, ws.paths.root / "slack"),
                             SlackSettings(bot_token="xoxb-test"), client,
                             detail=NoticeDetail(ws))  # fmt: skip
    channel.send(note, people(note["to"], directory(ws.paths.config, "DEMO01")))
    dm = json.dumps(client.posts[0]["blocks"])
    assert "Please send the ECG report." in dm and "More documents needed" in dm
    assert "Please send" not in json.dumps(note)  # the e-mail's note is unchanged

    notice_ts = "1700000000.000001"
    ecg = pdf("ECG REPORT\nST elevation.")
    files = [attached("F7", "ecg.pdf", ecg)]
    agent = replay_agent(ws, tmp_path)
    agent.opener = Downloads({files[0]["url_private_download"]: ecg})
    answer(agent, "site101.coord", "(attached 1 file)", "Added ecg.pdf to DEMO01-0001.",
           [{"tool": "upload_attached_files", "input": {"case": CID}}], case_id=CID)  # fmt: skip
    thread = [{"bot_id": "B0APP", "text": client.texts[0], "ts": notice_ts}]
    reply = message("", ts="1700000400.0", thread_ts=notice_ts, subtype="file_share",
                    files=files)  # fmt: skip
    agent.on_message(reply, FilesSlack(thread))
    assert ws.store.state(CID).state == State.DEIDENTIFYING  # the query is answered
    assert log_of(ws.paths)[-1]["case_id"] == CID


def test_a_query_text_with_identifiers_stays_out_of_the_dm(ws, monkeypatch):
    who = PhiGate.for_workspace(ws).identities[ws.store.state(CID).subject]
    query = {"type": "query.opened", "data": {"codes": ["wrong_patient"],
             "text": f"Page 2 names {who['first']} {who['last']}."}}  # fmt: skip
    monkeypatch.setattr("adjud.slack_app.dm.latest_query", lambda ws, cid: query)
    note = {"template": "query_opened", "case_id": CID}
    assert NoticeDetail(ws)(note) is None  # the DM then goes as the e-mail does
    query["data"]["text"] = "Page 2 is about another patient."
    assert "another patient" in (NoticeDetail(ws)(note) or "")
    assert NoticeDetail(ws)({"template": "documents_requested", "case_id": CID}) is None


def test_after_the_signature_the_agent_shows_the_signed_narrative(ws, in_qc_review):
    tools = acting(ws, "ss.anna", [], Downloads({}))
    draft = call(tools, "narrative_draft", case="0001")
    assert draft["approved"] is False
    edited = "Subject DEMO01-101-0001 had a myocardial infarction. EDITED BY THE SS."
    assert edited not in draft["text"]
    in_qc_review.flow2.sign_qc_pass(CID, "ss.anna", "demo", edited, "Documents present")
    signed = call(tools, "narrative_draft", case="0001")
    assert signed["approved"] is True and signed["edited_unsigned"] is False
    assert signed["text"].strip() == edited  # the signed file, not the earlier draft


def test_a_slack_file_goes_into_one_case_only(ws):
    """A file added to one case earlier in the thread is not added to the next case too."""
    a, b = pdf("DISCHARGE SUMMARY\nChest pain."), pdf("ECG REPORT\nST elevation.")
    f1, f2 = attached("F1", "a.pdf", a), attached("F2", "b.pdf", b)
    downloads = Downloads({f1["url_private_download"]: a, f2["url_private_download"]: b})
    call(acting(ws, "site101.coord", [f1], downloads), "upload_attached_files", case=CID)
    call(acting(ws, "site101.coord", [f1, f2], downloads), "upload_attached_files",
         case="DEMO01-0003")  # fmt: skip
    (up,) = uploads(ws, "DEMO01-0003")
    assert [f["name"] for f in up["data"]["files"]] == ["b.pdf"]
    with pytest.raises(ToolError, match="already in the case"):
        call(acting(ws, "site101.coord", [f1, f2], downloads), "upload_attached_files",
             case="DEMO01-0003")  # fmt: skip


def test_a_file_attached_as_one_site_is_not_uploaded_as_another(ws, tmp_path):
    """Acting As switched from site 101 to site 102 in the same thread: the file attached
    as site 101 is not offered to site 102's upload (Q5); back as site 101, it is."""
    agent = replay_agent(ws, tmp_path)
    first = "1700000100.000100"
    f5 = attached("F5", "letter.pdf", pdf("LETTER"))
    attached_as(ws, "site101.coord", first, ["F5"])
    client = FilesSlack([{"user": ME, "text": "", "files": [f5], "subtype": "file_share",
                          "ts": first}])  # fmt: skip
    event = message("for my case", ts="1700000102.0", thread_ts=first)

    def offered(person_id: str) -> list[str]:
        earlier: list[dict] = []
        agent._history(client, event, DM, first, earlier)
        turn = agent._turn_of(event, client, earlier, ws.people.by_id[person_id])
        return [f["id"] for f in turn.attachments]

    assert offered("site102.coord") == []
    assert offered("site101.coord") == ["F5"]


def test_the_quality_check_notice_carries_the_narrative(ws, in_qc_review, monkeypatch):
    """P-59 (Nirav's review): the Safety Specialist reads the narrative in the notice,
    without asking the Agent for it; the e-mail's note is unchanged. A narrative the PHI
    gate holds is replaced by a line saying so."""
    note = next(n for n in DeliveryLog(ws.store).notes() if n["template"] == "qc_review_needed")
    ws.people.act_as(ME, "ss.anna")
    client = FilesSlack()
    channel = SlackDmChannel(Directory(ws.paths.config, ws.paths.root / "slack"),
                             SlackSettings(bot_token="xoxb-test"), client,
                             detail=NoticeDetail(ws))  # fmt: skip
    channel.send(note, people(note["to"], directory(ws.paths.config, "DEMO01")))
    dm = json.dumps(client.posts[0]["blocks"])
    first = qc_drafts(ws.store, CID).narrative or ""
    drafted = next(e for e in reversed(ws.store.events(CID)) if e["type"] == "narrative.drafted")
    text = ws.store.get_file(CID, drafted["data"]["draft"]["path"]).decode().strip()
    assert not first and text.splitlines()[0] in json.loads(dm)[1]["text"]["text"]
    assert "Narrative draft (" in dm and "not signed" in dm
    assert "Narrative draft" not in json.dumps(note)

    Flow2(ws.store, ws.paths.config).save_narrative_draft(CID, "ss.anna", "Edited text.")
    assert "your unsigned edit" in NoticeDetail(ws)(note) and "Edited text." in (
        NoticeDetail(ws)(note) or ""
    )
    who = PhiGate.for_workspace(ws).identities[ws.store.state(CID).subject]
    monkeypatch.setattr("adjud.slack_app.dm.narrative_now",
                        lambda ws, cid: (f"{who['first']} {who['last']} had chest pain.",
                                         {"edited": True}))  # fmt: skip
    assert NoticeDetail(ws)(note) == NARRATIVE_HELD


def test_the_narrative_survives_a_query_to_the_site(ws, in_qc_review):
    """P-59 (Nirav's review): the Safety Specialist's narrative edit is kept when a query
    goes to the site; the next round starts from it, while the query draft does not carry
    over."""
    flow2 = Flow2(ws.store, ws.paths.config)
    flow2.save_narrative_draft(CID, "ss.anna", "My careful narrative.", via="app")
    flow2.save_query_draft(CID, "ss.anna", ["missing_document"], "Send the ECG.")
    flow2.open_query(CID, ["missing_document"], "Send the ECG.", user_id="ss.anna",
                     password="demo")  # fmt: skip
    upload(ws.store, CID, [("ecg2.pdf", pdf("ECG REPORT\nRepeat ECG."))], "site101.coord")
    to_qc_review(in_qc_review, ws.store, CID)
    drafts = qc_drafts(ws.store, CID)
    assert drafts.narrative.strip() == "My careful narrative."
    assert drafts.narrative_round == 1 and drafts.narrative_via == "app"
    assert drafts.query_text is None and drafts.latest is None  # the query was sent


def test_a_narrative_edit_on_the_page_is_saved_as_a_draft(ws, in_qc_review):
    """P-59: an edit on the Quality check page is saved unsigned, so a refresh or a new
    tab from Slack starts from it."""
    at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=30)
    at.session_state["role"], at.session_state["case_id"] = "safety_specialist", CID
    at.run()
    at.switch_page("views/quality_check.py")
    at.run()
    next(r for r in at.radio if r.label == "Your decision").set_value("pass").run()
    next(a for a in at.text_area if a.label == "Narrative").set_value("Edited here.").run()
    assert not at.exception, at.exception
    drafts = qc_drafts(ws.store, CID)
    assert drafts.narrative.strip() == "Edited here." and drafts.narrative_via == "app"

    fresh = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=30)  # a new tab
    fresh.session_state["role"], fresh.session_state["case_id"] = "safety_specialist", CID
    fresh.run()
    fresh.switch_page("views/quality_check.py")
    fresh.run()
    next(r for r in fresh.radio if r.label == "Your decision").set_value("pass").run()
    assert next(a for a in fresh.text_area if a.label == "Narrative").value == "Edited here.\n"
    assert ws.store.state(CID).state == State.QC_REVIEW  # saved, never signed
