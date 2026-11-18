"""The Slack app (plan P-55, ADR 0003, ADR 0004) with a fake Slack client: nothing here
reaches Slack. Acting As, /adj, App Home, the data manager's import, the Slack actions
log, and a check of every payload the app builds against the Clinical Content list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adjud.documents import upload
from adjud.integrity import verify_all
from adjud.paths import DataPaths
from adjud.runner import Runner
from adjud.slack_app import blocks
from adjud.slack_app.app import SlackHandlers, build_app
from adjud.slack_app.core import SlackActionsLog, Workspace, suggested_word
from adjud.workflow import State
from tests.test_flow3 import SITE, pdf, through_qc, to_qc_review
from tools.reset_demo import DEMO_AES, reset

ROOT = Path(__file__).resolve().parents[1]
ME = "U0PRESENTER"


class FakeClient:
    def __init__(self):
        self.published: list[dict] = []
        self.opened: list[dict] = []
        self.posts: list[dict] = []

    def chat_postMessage(self, **kwargs):  # noqa: N802 (slack_sdk's name)
        self.posts.append(kwargs)
        return {"ok": True, "channel": "D0PRESENTER", "ts": f"{len(self.posts)}.0"}

    def views_publish(self, user_id, view):
        self.published.append({"user_id": user_id, "view": view})

    def views_open(self, trigger_id, view):
        self.opened.append({"trigger_id": trigger_id, "view": view})


class Replies:
    def __init__(self):
        self.sent: list[dict] = []

    def __call__(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def last(self) -> str:
        return json.dumps(self.sent[-1])


def ack(*args, **kwargs):
    ack.last = kwargs


@pytest.fixture
def data(tmp_path, monkeypatch) -> DataPaths:
    root = tmp_path / "data"
    root.mkdir()
    (root / "config").symlink_to(ROOT / "data" / "config")
    monkeypatch.setenv("ADJUD_DATA_DIR", str(root))
    paths = DataPaths(root)
    reset(paths, preload=True, only=DEMO_AES)
    return paths


@pytest.fixture
def app(data):
    ws = Workspace(data)
    return SlackHandlers(ws, SlackActionsLog(data)), FakeClient()


def command(h, client, text: str, user: str = ME) -> Replies:
    r = Replies()
    h.on_command(ack, {"user_id": user, "text": text, "trigger_id": "T1"}, r, client)
    return r


def actions(data) -> list[dict]:
    return SlackActionsLog(data).log.records()


def test_nothing_works_before_choosing_whom_to_act_as(app, data):
    h, client = app
    r = command(h, client, "tasks")
    assert "Choose whom you act as first" in r.last
    assert r.sent[-1]["response_type"] == "ephemeral"
    assert actions(data)[-1]["result"] == "refused"


def test_act_as_by_name_publishes_that_persons_home(app, data):
    h, client = app
    r = command(h, client, "act-as rahul")
    assert "You now act as Rahul Mehta (Data manager)" in r.last
    home = json.dumps(client.published[-1]["view"])
    assert client.published[-1]["user_id"] == ME
    assert "Acting as *Rahul Mehta (Data manager)*" in home
    assert "AE file waiting to be imported" in home and "import_queued" in home
    entry = actions(data)[-1]
    assert (entry["slack_user"], entry["acting_as"], entry["action"]) == (ME, "dm.dana", "act_as")
    assert "matches" in command(h, client, "act-as dr").last  # three doctors: be precise


def test_the_act_as_form_sets_the_person_or_says_what_is_missing(app, data):
    h, client = app
    command(h, client, "act-as")
    modal = client.opened[-1]["view"]
    assert modal["callback_id"] == "act_as" and client.opened[-1]["trigger_id"] == "T1"
    roles = [g["label"]["text"] for g in modal["blocks"][0]["element"]["option_groups"]]
    assert roles[0] == "Data manager" and "Adjudicator" in roles
    body = {"user": {"id": ME}}
    empty = {"state": {"values": {"who": {"person": {"selected_option": None}}}}}
    h.on_act_as_submitted(ack, body, empty, client)
    assert ack.last == {"response_action": "errors", "errors": {"who": "Choose a person."}}
    chosen = {"state": {"values": {"who": {"person": {"selected_option": {"value": "ss.anna"}}}}}}
    h.on_act_as_submitted(ack, body, chosen, client)
    assert h.people.person_for(ME)["id"] == "ss.anna"


def test_the_data_manager_imports_from_slack_and_the_log_names_the_cases(app, data):
    h, client = app
    command(h, client, "act-as rahul")
    r = command(h, client, "import")
    assert (
        "Imported 1 file: 4 records; 3 new cases (DEMO01-0001, DEMO01-0002, DEMO01-0003)" in r.last
    )
    assert "1 not a new case" in r.last and "AE-0" not in r.last  # no AE or subject IDs
    entry = actions(data)[-1]
    assert entry["action"] == "import" and entry["cases"] == ["DEMO01-0001", "DEMO01-0002",
                                                             "DEMO01-0003"]  # fmt: skip
    created = h.ws.store.events("DEMO01-0001")[0]
    assert created["actor"] == "dm.dana"  # the case event is as it would be from the app
    assert "Imported 1 file" in json.dumps(client.published[-1]["view"])
    assert "no AE file waiting" in command(h, client, "import").last


def test_the_import_button_on_app_home_does_the_same(app, data):
    h, client = app
    command(h, client, "act-as rahul")
    h.on_import_button(ack, {"user": {"id": ME}}, client)
    assert len(h.ws.store.list_cases()) == 3
    assert actions(data)[-1]["action"] == "import"


def test_only_the_data_manager_may_import(app, data):
    h, client = app
    command(h, client, "act-as anna")
    assert "data manager's step" in command(h, client, "import").last
    assert actions(data)[-1]["result"] == "refused" and h.ws.store.list_cases() == []


def test_a_site_sees_its_own_cases_and_tasks_open_the_app(app, data):
    h, client = app
    command(h, client, "act-as rahul")
    command(h, client, "import")
    command(h, client, "act-as priya")
    r = command(h, client, "tasks")
    assert "Upload the documents" in r.last and "DEMO01-0001" in r.last
    assert "DEMO01-0002" not in r.last  # site 102's case
    button = next(b["accessory"] for b in r.sent[-1]["blocks"] if "accessory" in b)
    assert button["url"] == ("http://localhost:8501/documents?role=site&user=site101.coord"
                             "&case=DEMO01-0001")  # fmt: skip
    assert "Waiting for documents since" in command(h, client, "status 0001").last
    assert "Which case?" in command(h, client, "status 0002").last  # not theirs
    assert "Your cases (2)" in command(h, client, "cases").last  # 0001 and 0003 at site 101


def test_link_clicks_and_home_opening_are_handled(app, data):
    h, client = app
    command(h, client, "act-as anna")
    h.on_open_app(
        ack,
        {
            "user": {"id": ME},
            "actions": [
                {
                    "url": "http://localhost:8501/qc?role=safety_specialist&user=ss.anna&case=DEMO01-0001"
                }
            ],
        },
    )
    assert actions(data)[-1]["action"] == "open_app"
    assert actions(data)[-1]["cases"] == ["DEMO01-0001"]
    h.on_home_opened({"user": ME, "tab": "home"}, client)
    assert client.published[-1]["user_id"] == ME
    n = len(client.published)
    h.on_home_opened({"user": ME, "tab": "messages", "channel": "D0PRESENTER"}, client)
    assert len(client.published) == n  # the Messages tab is the Agent's (P-58): no Home
    assert "I act as *Dr. Anna Weber (Safety Specialist)*" in json.dumps(client.posts)


def test_help_and_unknown_commands_answer_privately(app, data):
    h, client = app
    assert "/adj status 0001" in command(h, client, "").last
    command(h, client, "act-as anna")
    assert "I don't know that one" in command(h, client, "frobnicate").last


def test_the_slack_actions_log_is_verified_and_survives_a_reset_without_acting_as(app, data):
    h, client = app
    command(h, client, "act-as rahul")
    log_path = data.root / "slack" / "actions.jsonl"
    assert any(r.log.name == "actions.jsonl" and r.ok for r in verify_all(data))
    log_path.write_text(log_path.read_text().replace("dm.dana", "ss.anna"))
    assert any(r.log.name == "actions.jsonl" and not r.ok for r in verify_all(data))
    reset(data, preload=True, only=DEMO_AES)
    assert not log_path.exists() and h.people.person_for(ME)["id"] == "dm.dana"


def test_the_bolt_app_registers_every_listener_without_calling_slack(app):
    """/adj, App Home (both tabs), three buttons, the Acting As form, and the Agent's
    events: message.im and four session events (P-58)."""
    h, _ = app
    bolt = build_app(h, "xoxb-test", verify=False)
    assert len(bolt._listeners) == 11


def forbidden(ws: Workspace) -> set[str]:
    """Clinical Content of every case (ADR 0004): the AE term, dates, subject and AE IDs,
    seriousness, outcome, severity, relatedness and codes (values of 5+ characters, so a
    'Y' does not match everything), document names, the AI Suggestion's text, and every
    line of each case's narrative and query text."""
    from adjud.documents import documents_of

    out: set[str] = set()
    for cid in ws.store.list_cases():
        snap = ws.pipe.latest_snapshot(cid)
        for key in ("ae_term", "onset_date", "end_date", "subject_id", "ae_id"):
            if snap.get(key):
                out.add(str(snap[key]).lower())
        for key in ("seriousness_criteria", "outcome", "severity", "relatedness_investigator",
                    "pt_code", "llt_code", "soc_code"):  # fmt: skip
            if len(str(snap.get(key) or "")) >= 5:
                out.add(str(snap[key]).lower())
        out |= {name.lower() for name in documents_of(ws.store, cid).files}
        for e in ws.store.events(cid):
            if e["type"] == "qc.checks_completed":
                sg = ws.store.get_json(cid, e["data"]["results"]["path"]).get("suggestion") or {}
                out |= {
                    t.lower()
                    for t in [sg.get("summary") or ""]
                    + [f.get("reason") or "" for f in sg.get("fields") or []]
                    if len(t) > 15
                }
            if e["type"] == "query.opened" and e["data"].get("text"):
                out.add(e["data"]["text"].lower())
            if e["type"] == "narrative.drafted":
                text = ws.store.get_file(cid, e["data"]["draft"]["path"]).decode()
                out |= {ln.strip().lower() for ln in text.splitlines() if len(ln.strip()) > 25}
    return out


def test_no_payload_carries_clinical_content(data):
    """Every App Home, reply and DM the app builds, for every person, with cases at
    several steps (one through the Quality Check, with a narrative and adjudication)."""
    runner = Runner(data)
    runner.run_once()
    store = runner.pipeline.store
    upload(store, "DEMO01-0001", [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain.")),
                                  ("ecg.pdf", pdf("ECG REPORT\nST elevation."))], SITE)  # fmt: skip
    through_qc(runner, store, "DEMO01-0001")
    assert store.state("DEMO01-0001").state == State.ADJ_REVIEW
    upload(store, "DEMO01-0003", [("note.pdf", pdf("CLINIC NOTE\nChest pain."))], SITE)
    to_qc_review(runner, store, "DEMO01-0003")  # the Safety Specialist's row and status
    ws = Workspace(data)
    dm = ws.people.by_id["dm.dana"]
    pulled, _ = ws.pull_from_edc(dm)  # before the forbidden list, so its cases are in it
    bad = forbidden(ws)
    assert len(bad) > 10  # AE terms, dates, IDs and narrative lines of three cases
    payloads: list = [pulled]
    assert "suggested: " in json.dumps(blocks.home(ws, ws.people.by_id["ss.anna"]))
    assert "not a case" in json.dumps(blocks.home(ws, dm))
    for person in ws.people.users:
        payloads.append(blocks.home(ws, person))
        payloads.append(blocks.tasks_reply(ws, person))
        payloads.append(blocks.cases_reply(ws, person))
        for cs in ws.cases(person):
            payloads.append(blocks.status_reply(ws, person, ws.status(person, cs.case_id)))
    from adjud.delivery import DeliveryLog, directory, people
    from adjud.slack_app.dm import for_whom

    users = directory(data.config, "DEMO01")
    for note in DeliveryLog(store).notes():
        who = for_whom(note["to"], people(note["to"], users), ws.people)
        payloads.append(blocks.notice(note, who, "http://localhost:8501" + (note["link"] or "")))
    text = json.dumps(payloads).lower()
    leaks = sorted(b for b in bad if b in text)
    assert not leaks, leaks


def test_every_interaction_is_logged_without_the_typed_text(app, data):
    h, client = app
    h.on_home_opened({"user": ME, "tab": "home"}, client)
    assert actions(data)[-1]["action"] == "home_opened"
    command(h, client, "DEMO01-101-0001 chest pain")  # nobody acting yet
    command(h, client, "act-as rahul")
    command(h, client, "myocardial infarction")
    logged = (data.root / "slack" / "actions.jsonl").read_text().lower()
    assert "chest pain" not in logged and "infarction" not in logged and "101-0001" not in logged
    assert [a["action"] for a in actions(data)][-3:] == ["command:unknown", "act_as",
                                                         "command:unknown"]  # fmt: skip
    empty = {"state": {"values": {"who": {"person": {"selected_option": None}}}}}
    h.on_act_as_submitted(ack, {"user": {"id": ME}}, empty, client)
    assert actions(data)[-1]["result"] == "refused"


def test_hidden_files_in_the_queue_are_not_an_import_task(app, data):
    h, client = app
    for f in data.inbox.iterdir():
        f.unlink()
    (data.inbox / ".DS_Store").write_text("")
    command(h, client, "act-as rahul")
    assert "waiting to be imported" not in command(h, client, "tasks").last
    assert "no AE file waiting" in command(h, client, "import").last


def test_a_failed_import_is_answered_and_logged(app, data, monkeypatch):
    h, client = app
    command(h, client, "act-as rahul")

    def taken(*a, **kw):
        raise FileNotFoundError("the runner moved it")

    monkeypatch.setattr(h.ws.pipe, "process_inbox", taken)
    assert "did not finish" in command(h, client, "import").last
    assert actions(data)[-1]["result"] == "failed"
    assert actions(data)[-1]["reason"] == "FileNotFoundError"


def test_the_safety_specialist_sees_the_suggested_outcome_as_one_word(app, data):
    h, client = app
    runner = Runner(data)
    runner.run_once()
    store = runner.pipeline.store
    upload(store, "DEMO01-0001", [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain."))], SITE)
    to_qc_review(runner, store, "DEMO01-0001")
    proposal = next(e["data"]["proposal"] for e in reversed(store.events("DEMO01-0001"))
                    if e["type"] == "qc.checks_completed")  # fmt: skip
    word = suggested_word(proposal)
    command(h, client, "act-as anna")
    rows = [b["text"]["text"] for b in client.published[-1]["view"]["blocks"] if "text" in b]
    assert "*DEMO01-0001* · Review the Quality Check and sign · suggested: " + word in rows
    assert f"suggested: {word}" in command(h, client, "status 0001").last
    for other in ("rahul", "priya", "sofia"):  # data manager, site, CEC Coordinator
        command(h, client, f"act-as {other}")
        assert "suggested" not in json.dumps(client.published[-1]["view"])
        assert "suggested" not in command(h, client, "status 0001").last


def test_the_suggested_outcome_words_are_pass_query_or_none():
    assert [suggested_word(p) for p in ("pass", "query", "unclear", None)] == [
        "pass", "query", "none", "none"]  # fmt: skip


def test_the_data_manager_sees_how_many_records_are_not_a_case(app, data):
    h, client = app
    command(h, client, "act-as rahul")
    assert "not a case" not in json.dumps(client.published[-1]["view"])
    command(h, client, "import")
    home = client.published[-1]["view"]
    row = next(b for b in home["blocks"] if "not a case" in json.dumps(b))
    assert row["text"]["text"] == "1 record is not a case. The reasons are on AE import."
    assert row["accessory"]["text"]["text"] == "Open AE import"
    assert row["accessory"]["url"] == "http://localhost:8501/import?role=data_manager&user=dm.dana"
    assert row["accessory"]["action_id"].startswith("open_app")
    command(h, client, "act-as anna")
    assert "not a case" not in json.dumps(client.published[-1]["view"])


def test_the_data_manager_pulls_from_the_edc_from_slack(app, data):
    h, client = app
    command(h, client, "act-as anna")
    assert "data manager's step" in command(h, client, "pull").last
    assert actions(data)[-1]["action"] == "pull" and actions(data)[-1]["result"] == "refused"
    assert h.ws.store.list_cases() == []
    command(h, client, "act-as rahul")
    r = command(h, client, "pull")
    created = sorted(h.ws.store.list_cases())
    assert created and "Pulled the AE records from the EDC. Imported 2 files" in r.last
    assert all(c in r.last for c in created) and "AE-0" not in r.last
    entry = actions(data)[-1]
    assert (entry["action"], entry["result"], sorted(entry["cases"])) == ("pull", "done", created)
    assert "Pulled the AE records" in json.dumps(client.published[-1]["view"])
    assert "/adj pull" in command(h, client, "help").last
