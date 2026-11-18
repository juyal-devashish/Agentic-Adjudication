"""The Slack Agent demo (plan P-58), end to end: DEMO01-0001 from Rahul asking the Agent
to import, through Priya attaching the kit's three documents in the thread of her
"documents requested" notice, the runner removing the identifiers and Priya signing in
the app, to Dr Weber asking the Agent about the Quality Check, editing the narrative
draft through it and signing in the app, until the case waits for the adjudicators.

Each message gets one reply, in its thread, saying what the tools found; and no payload
the app sends to Slack (DMs, the Agent's replies, App Home, /adj replies) carries any
identifier the kit seeded in the documents (ADR 0005: PHI never leaves the app).

A fake Slack client and a fake file download stand in for Slack; the Agent runs on
replay with answers written in the test's folder from what its tools return (labelled
simulated), and the QC agents on their recorded replay: nothing reaches Slack or the API."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from adjud.flow2 import qc_drafts
from adjud.paths import DataPaths
from adjud.review import submit
from adjud.runner import Runner
from adjud.slack_app.agent import SlackAgent
from adjud.slack_app.agent_tools import agent_toolbox
from adjud.slack_app.app import SlackHandlers
from adjud.slack_app.core import SlackActionsLog, Workspace
from adjud.slack_app.dm import SlackSettings
from adjud.workflow import State
from tests.test_slack_agent import DM, ME, answer, log_of, message, replay_agent
from tests.test_slack_agent_actions import Downloads, FilesSlack, attached
from tools.reset_demo import DEMO_AES, reset

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
CASE = "DEMO01-0001"
# what de-identification must remove for a patient (deid_policy.yaml must_redact); the
# facility and staff fields are should_redact there, reported and not gated, so not here
IDENTITY_FIELDS = ("dob", "address", "phone", "mobile", "email", "mrn", "insurance", "kin")


# -- the kit's seeded identifiers, and a check of payloads against them --------------------------


def seeded_identifiers() -> set[str]:
    """Every must-redact value the kit seeded (ground truth), and each subject's identity:
    full name both ways round, surname, date of birth (ISO and as written), address,
    phones, e-mail, record and insurance numbers, next of kin. Lower case."""
    out: set[str] = set()
    for f in sorted((KIT / "ground_truth").glob("D*.json")):
        out |= {i["value"] for i in json.loads(f.read_text())["items"]
                if i["requirement"] == "must_redact"}  # fmt: skip
    for f in sorted((KIT / "identities").glob("*.json")):
        for who in json.loads(f.read_text())["subjects"].values():
            out |= {f"{who['first']} {who['last']}", f"{who['last']}, {who['first']}",
                    who["last"]}  # fmt: skip
            out |= {str(who[k]) for k in IDENTITY_FIELDS if who.get(k)}
            y, m, d = str(who["dob"]).split("-")
            out.add(f"{d}/{m}/{y}")
    return {v.lower() for v in out if v.strip()}


def strings(payload: Any) -> list[str]:
    """Every string in a payload (texts, blocks, URLs, views), as Slack would get it."""
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        return [s for v in payload.values() for s in strings(v)]
    if isinstance(payload, list | tuple):
        return [s for v in payload for s in strings(v)]
    return []


def phi_in(payloads: Any) -> list[str]:
    """The seeded identifiers found in any string of the payloads (whole words only, so
    a surname never matches inside another word; `_` separates words, as in file names)."""
    found: set[str] = set()
    texts = [s.lower() for s in strings(payloads)]
    for value in seeded_identifiers():
        pattern = re.compile(r"(?<![^\W_])" + re.escape(value) + r"(?![^\W_])")
        if any(pattern.search(t) for t in texts):
            found.add(value)
    return sorted(found)


def test_the_seeded_identifiers_are_the_kits_and_none_is_an_app_users_name():
    ids = seeded_identifiers()
    assert len(ids) > 80
    assert {"philip roberts", "roberts, philip", "24/07/1955", "1955-07-24", "2929647",
            "philip.roberts1@example.org", "wendy roberts"} <= ids  # fmt: skip
    users = yaml.safe_load((ROOT / "data" / "config" / "users.yaml").read_text())["users"]
    words = {w.lower().strip(".") for u in users for w in u["name"].split()}
    assert not ids & words  # a demo user's name is never taken for a patient's
    assert phi_in([{"text": "fine"}, ["Case DEMO01-0001: *Philip Roberts*"]]) == [
        "philip roberts", "roberts"]  # fmt: skip
    assert phi_in({"text": "Carry on; DEMO01-101-0001 had a myocardial infarction."}) == []
    assert phi_in({"text": "Added `Roberts_Philip_letter.pdf`."}) == ["roberts"]


# -- Slack, as the whole demo uses it ----------------------------------------------------------


class SlackWorld(FilesSlack):
    """One DM between the presenter and the app: every post the app makes (notices and the
    Agent's replies, each with its own `ts`), the messages the person sends, threads as
    `conversations.replies` gives them, and the App Home views published."""

    def __init__(self) -> None:
        super().__init__()
        self.history: list[dict[str, Any]] = []
        self.views: list[dict[str, Any]] = []
        self.n = 0

    def _ts(self) -> str:
        self.n += 1
        return f"1700001{self.n:03d}.000100"

    def chat_postMessage(self, **kwargs):  # noqa: N802 (slack_sdk's name)
        self.posts.append(kwargs)
        ts = self._ts()
        self.history.append({"bot_id": "B0APP", "text": kwargs.get("text") or "", "ts": ts,
                             "thread_ts": kwargs.get("thread_ts") or ts})  # fmt: skip
        return {"ok": True, "channel": DM, "ts": ts}

    def conversations_replies(self, *, channel, ts, limit):
        return {"messages": [m for m in self.history if m["thread_ts"] == ts][:limit]}

    def views_publish(self, user_id, view):
        self.views.append(view)

    def views_open(self, trigger_id, view):
        self.views.append(view)

    def notice(self, about: str) -> str:
        """The `ts` of the latest DM about this case and task: its thread's start."""
        return next(m["ts"] for m in reversed(self.history)
                    if m["thread_ts"] == m["ts"] and about in m["text"])  # fmt: skip

    def say(self, agent: SlackAgent, text: str, thread: str | None = None,
            files: list[dict] | None = None) -> dict[str, Any]:  # fmt: skip
        """The person's message, with the edit and the app's own echo Slack also sends;
        returns the one reply, which must be in the message's thread."""
        ts = self._ts()
        extra: dict[str, Any] = {"thread_ts": thread} if thread else {}
        if files:
            extra |= {"subtype": "file_share", "files": files}
        event = message(text, ts=ts, **extra)
        self.history.append({**event, "thread_ts": thread or ts})
        posts, statuses = len(self.posts), len(self.statuses)
        for e in (event, {**event, "subtype": "message_changed"},
                  {**event, "bot_id": "B0APP", "user": None}):  # fmt: skip
            agent.on_message(e, self)
        (reply,) = self.posts[posts:]
        assert reply["thread_ts"] == (thread or ts) and reply["channel"] == DM
        assert self.statuses[statuses:] == ["processing", "active"]
        return reply


class Replies:
    """What /adj answers, privately."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def __call__(self, **kwargs):
        self.sent.append(kwargs)


def dm_task(post: dict) -> str | None:
    found = re.search(rf"Case {CASE}: (.+?)\*", post["blocks"][0]["text"]["text"])
    return found.group(1) if found else None


def tool(ws: Workspace, person_id: str, name: str, **args) -> dict:
    """What the Agent's tool gives this person now (the answer is written from it)."""
    return json.loads(agent_toolbox(ws, ws.people.by_id[person_id]).call(name, args))


# -- the demo ------------------------------------------------------------------------------------


def test_the_slack_agent_demo_runs_from_the_import_to_the_adjudicators(tmp_path, monkeypatch):
    monkeypatch.setenv("ADJUD_AI_MODE", "replay")  # the QC agents: identical every run
    monkeypatch.setenv("NOTIFY_CHANNELS", "outbox,slack")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-secret-token")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    slack = SlackWorld()
    monkeypatch.setattr(SlackSettings, "client", lambda self: slack)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)  # the seeded identities the PHI gate knows
    paths = DataPaths(data)
    reset(paths, preload=True, only=DEMO_AES)
    ws = Workspace(paths)
    agent = replay_agent(ws, tmp_path)
    folder = data / "demo_uploads" / "documents" / "DEMO01-101-0001_AE-001"
    kit_files = {f.name: f.read_bytes() for f in sorted(folder.iterdir())}
    files = [attached(f"F{i}", name, content)
             for i, (name, content) in enumerate(kit_files.items(), 1)]  # fmt: skip
    agent.opener = Downloads({f["url_private_download"]: kit_files[f["name"]] for f in files})
    handlers = SlackHandlers(ws, SlackActionsLog(paths), agent=agent)
    replies = Replies()

    def adj(text: str) -> str:
        """/adj as the presenter; the answer's text."""
        handlers.on_command(lambda *a, **k: None, {"user_id": ME, "text": text,
                                                   "trigger_id": "T"}, replies, slack)  # fmt: skip
        return json.dumps(replies.sent[-1])

    def look_around() -> None:
        """The presenter's App Home and /adj answers at this step."""
        handlers.on_home_opened({"user": ME, "tab": "home"}, slack)
        adj("tasks")
        adj("cases")
        adj(f"status {CASE}")

    adj("act-as rahul")
    runner = Runner(paths)
    store = runner.pipeline.store
    runner.tick(0)  # "AE records are waiting to be imported", with Import in the DM
    assert slack.texts == ["[DEMO01] AE records are waiting to be imported"]

    # 1. Rahul asks the Agent to import: the cases are created as him
    answer(agent, "dm.dana", "Import the waiting AE records, please",
           "Imported 1 file: 3 new cases, DEMO01-0001 to DEMO01-0003.",
           [{"tool": "import_waiting_files", "input": {}}])  # fmt: skip
    reply = slack.say(agent, "Import the waiting AE records, please")
    assert reply["text"] == "Imported 1 file: 3 new cases, DEMO01-0001 to DEMO01-0003."
    assert sorted(store.list_cases()) == ["DEMO01-0001", "DEMO01-0002", "DEMO01-0003"]
    assert {e["actor"] for e in store.events(CASE) if e["type"] == "case.created"} == {"dm.dana"}
    look_around()
    runner.tick(1)
    assert [dm_task(p) for p in slack.posts[-3:] if dm_task(p)] == [
        "documents requested for an adjudication case"]  # fmt: skip

    # 2. Priya answers that notice with the three kit documents attached
    assert "Priya Nair" in adj("act-as priya")
    asked = slack.notice(f"Case {CASE}: documents requested")
    added = ("Added 3 files to DEMO01-0001. Identifiers are being removed now; review and "
             "sign the redacted pages in the app.")  # fmt: skip
    answer(agent, "site101.coord", "Here are the documents\n\n(attached 3 files)", added,
           [{"tool": "upload_attached_files", "input": {"case": CASE}}], case_id=CASE)  # fmt: skip
    reply = slack.say(agent, "Here are the documents", thread=asked, files=files)
    assert reply["text"] == added
    (up,) = [e for e in store.events(CASE) if e["type"] == "documents.uploaded"]
    assert up["actor"] == "site101.coord" and up["data"]["via"] == {"slack_files":
                                                                    ["F1", "F2", "F3"]}  # fmt: skip
    assert slack.deleted == ["F1", "F2", "F3"]  # tried; Slack keeps a person's file (Q8)
    runner.tick(2)  # identifiers removed and verified
    assert store.state(CASE).state == State.AWAITING_SITE_CONFIRMATION
    assert dm_task(slack.posts[-1]) == "redacted documents are ready for your review"

    # Priya asks how the redaction went, in the thread of that notice
    ready = slack.notice(f"Case {CASE}: redacted documents")
    results = tool(ws, "site101.coord", "redaction_results", case=CASE)
    told = "Redaction results for `DEMO01-0001`:\n" + "\n".join(
        f"• {f['file']}: {f['status']}, {f['identifiers_removed']} identifiers removed, "
        f"{f['identifiers_left']} left" for f in results["files"])  # fmt: skip
    answer(agent, "site101.coord", "How did the redaction go?", told,
           [{"tool": "redaction_results", "input": {"case": CASE}}], case_id=CASE)  # fmt: skip
    reply = slack.say(agent, "How did the redaction go?", thread=ready)
    assert reply["text"] == told and len(results["files"]) == 3
    look_around()
    submit(store, CASE, "site101.coord", "demo", paths.config)  # signed in the app
    runner.tick(3)  # handoff to Flow 2
    runner.tick(4)  # the Quality Check round, agents on replay
    assert store.state(CASE).state == State.QC_REVIEW
    assert dm_task(slack.posts[-1]) == "a quality check needs your review"

    # 3. Dr Weber asks about the Quality Check, then edits the narrative through the Agent
    assert "Dr. Anna Weber" in adj("act-as anna")
    review = slack.notice(f"Case {CASE}: a quality check needs your review")
    checks = tool(ws, "ss.anna", "qc_checks", case=CASE)
    suggestion = tool(ws, "ss.anna", "ai_suggestion", case=CASE)
    assert suggestion["summary"] and suggestion["suggested_outcome"] == "pass"
    qc_said = (f"The Quality Check of `{CASE}`: "
               + "; ".join(f"{c['check']}: {c['result']}" for c in checks["checks"])
               + f".\n*AI Suggestion* (suggested: {suggestion['suggested_outcome']}): "
               + suggestion["summary"])  # fmt: skip
    asked_qc = "What does the Quality Check say?"
    answer(agent, "ss.anna", asked_qc, qc_said,
           [{"tool": "qc_checks", "input": {"case": CASE}},
            {"tool": "ai_suggestion", "input": {"case": CASE}}], case_id=CASE)  # fmt: skip
    reply = slack.say(agent, asked_qc, thread=review)
    assert reply["text"] == qc_said and "Simulated AI" in json.dumps(reply["blocks"])

    draft = tool(ws, "ss.anna", "narrative_draft", case=CASE)["text"]
    edited = draft.rstrip() + "\nThe event resolved after percutaneous coronary intervention.\n"
    asked_edit = "Add that the event resolved after PCI to the narrative."
    saved = (
        "Saved the narrative draft, unsigned. It now ends: "
        + edited.strip().splitlines()[-1]
        + " Sign it with the QC pass in the app."
    )
    answer(agent, "ss.anna", [asked_qc, qc_said, asked_edit], saved,
           [{"tool": "edit_narrative_draft", "input": {"case": CASE, "text": edited}}],
           case_id=CASE)  # fmt: skip
    reply = slack.say(agent, asked_edit, thread=review)
    assert reply["text"] == saved
    url = reply["blocks"][-2]["elements"][0]["url"]  # the signature is in the app
    assert "/qc?" in url and "user=ss.anna" in url and f"case={CASE}" in url
    assert store.state(CASE).state == State.QC_REVIEW  # nothing moves from Slack
    look_around()

    # 4. Dr Weber signs in the app, from the draft the page starts with
    narrative = qc_drafts(store, CASE).narrative
    assert narrative == edited
    runner.flow2.sign_qc_pass(CASE, "ss.anna", "demo", narrative)
    runner.tick(5)  # the Flow 3 package
    runner.tick(6)  # Adjudicators 1 and 2 assigned
    assert store.state(CASE).state == State.ADJ_REVIEW
    adj("act-as okafor")
    look_around()

    # one reply per message, each logged as posted, never held
    turns = [e for e in log_of(paths) if e["kind"] == "turn"]
    assert [(e["acting_as"], e["outcome"], e["source"]) for e in turns] == [
        ("dm.dana", "posted", "ai"), ("site101.coord", "posted", "ai"),
        ("site101.coord", "posted", "ai"), ("ss.anna", "posted", "ai"),
        ("ss.anna", "posted", "ai")]  # fmt: skip
    assert [e["case_id"] for e in turns[1:]] == [CASE] * 4

    # what Slack got: P-58 allows the clinical content, never a seeded identifier
    sent = [slack.posts, slack.views, replies.sent]
    shown = " ".join(strings(sent))
    assert "myocardial infarction" in shown.lower() and suggestion["summary"] in shown
    assert "percutaneous coronary intervention" in shown  # the narrative went to Slack
    assert {"discharge_summary.pdf", "ecg_report.pdf"} <= set(re.findall(r"\w+\.pdf", shown))
    assert not phi_in(sent)
