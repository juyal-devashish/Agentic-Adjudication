"""The Slack demo (plan P-57): DEMO01-0001 from the Slack import to the adjudicators'
Assignment, through the runner with Slack on, as the presenter sees it. A fake Slack
client stands in for slack_sdk: nothing here reaches Slack. Also: resetting a separate
demo folder copies the committed config and leaves the repo's data alone."""

from __future__ import annotations

import json
import re
from pathlib import Path

from adjud.paths import DataPaths
from adjud.review import submit
from adjud.runner import Runner
from adjud.slack_app.app import SlackHandlers
from adjud.slack_app.core import SlackActionsLog, Workspace
from adjud.slack_app.dm import SlackSettings
from adjud.workflow import State
from tests.test_slack import PRESENTER, FakeClient
from tests.test_slack_app import forbidden
from tools.reset_demo import DEMO_AES, reset

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
CASE = "DEMO01-0001"


class FakeHomeClient:
    """Takes the App Home views the handlers publish."""

    def views_publish(self, user_id, view):
        pass


def dm_lines(posts: list[dict]) -> list[tuple[str, str, str, str | None]]:
    """(task, for whom, next step, app page) for each DM, as the presenter reads it."""
    out = []
    for p in posts:
        body = p["blocks"][0]["text"]["text"]
        task = re.search(r"Case \S+: (.+?)\*", body).group(1)
        who = re.search(r"For: (.+?) \(", body).group(1)
        nxt = re.search(r"Next: (.+)", body).group(1)
        urls = [e["url"] for b in p["blocks"] if b["type"] == "actions" for e in b["elements"]]
        page = urls[0].split("8501/")[1].split("?")[0] if urls else None
        out.append((task, who, nxt, page))
    return out


def test_the_slack_demo_runs_from_the_import_to_the_assignment(tmp_path, monkeypatch):
    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    monkeypatch.setenv("NOTIFY_CHANNELS", "outbox,slack")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-secret-token")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    client = FakeClient()
    monkeypatch.setattr(SlackSettings, "client", lambda self: client)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    paths = DataPaths(data)
    reset(paths, preload=True, only=DEMO_AES)
    ws = Workspace(paths)
    ws.people.act_as(PRESENTER, "dm.dana")  # /adj act-as rahul
    runner = Runner(paths)
    store = runner.pipeline.store
    seen = 0

    def new_dms() -> list[tuple[str, str, str, str | None]]:
        """This case's DMs since the last call, sorted: one tick's order is not fixed."""
        nonlocal seen
        fresh, seen = client.posts[seen:], len(client.posts)
        return sorted(dm_lines([p for p in fresh if f"Case {CASE}:" in p["text"]]))

    # 1. Rahul presses Import queued files on his App Home; the runner sends the DMs
    handlers = SlackHandlers(ws, SlackActionsLog(paths))
    handlers.on_import_button(lambda *a, **k: None, {"user": {"id": PRESENTER}, "trigger_id": "T"},
                              FakeHomeClient())  # fmt: skip
    assert runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001") == CASE
    runner.tick(0)
    assert new_dms() == [("documents requested for an adjudication case", "Priya Nair",
                          "Upload the documents", "documents")]  # fmt: skip
    # 2. Priya uploads in the app; identifiers are removed; she signs in the app
    folder = data / "demo_uploads" / "documents" / "DEMO01-101-0001_AE-001"
    from adjud.documents import upload

    upload(store, CASE, [(f.name, f.read_bytes()) for f in sorted(folder.iterdir())],
           "site101.coord")  # fmt: skip
    runner.tick(1)
    assert store.state(CASE).state == State.AWAITING_SITE_CONFIRMATION
    assert new_dms() == [("redacted documents are ready for your review", "Priya Nair",
                          "Review the redacted pages and sign", "documents")]  # fmt: skip
    submit(store, CASE, "site101.coord", "demo", paths.config)
    runner.tick(2)  # handoff to Flow 2
    assert new_dms() == [
        ("documents received, ready for QC", "Dr. Anna Weber",
         "Nothing more for you on this case now.", "qc"),  # the round is being prepared
        ("submission received", "Priya Nair",
         "Nothing more for you on this case now.", "documents"),
    ]  # fmt: skip
    runner.tick(3)  # the Quality Check round, agents in replay
    assert store.state(CASE).state == State.QC_REVIEW
    assert new_dms() == [("a quality check needs your review", "Dr. Anna Weber",
                          "Review the Quality Check and sign · suggested: pass", "qc")]  # fmt: skip
    # 3. Dr Weber signs the Quality Check in the app
    draft = next(e for e in store.events(CASE) if e["type"] == "narrative.drafted")
    text = store.get_file(CASE, draft["data"]["draft"]["path"]).decode()
    runner.flow2.sign_qc_pass(CASE, "ss.anna", "demo", text)
    # 4. The runner takes one step per case per pass (10 s each): the first writes the
    # Flow 3 package, the second assigns Adjudicators 1 and 2 and DMs them: 10-20 s
    runner.tick(4)
    assert store.state(CASE).state == State.READY_FOR_ADJUDICATION
    assert new_dms() == [("case ready for adjudication", "Dr. Anna Weber",
                          "Nothing more for you on this case now.", "record")]  # fmt: skip
    runner.tick(5)
    assert store.state(CASE).state == State.ADJ_REVIEW  # the end of the demo
    assert new_dms() == [
        ("an adjudication review is assigned to you", "Dr. Daniel Okafor",
         "Your independent review (round 1)", "adjudicate"),
        ("an adjudication review is assigned to you", "Dr. Elin Lindqvist",
         "Your independent review (round 1)", "adjudicate"),
    ]  # fmt: skip

    sent = json.dumps(client.posts).lower()
    assert not {w for w in forbidden(ws) if w in sent}  # ADR 0004


def test_a_reset_of_a_separate_demo_folder_brings_its_config_and_spares_data(tmp_path):
    """P-57: the demo runs in its own folder (ADJUD_DATA_DIR=data/demo), so starting over
    never touches the presenter's ./data. The committed config is copied into a folder
    that has none; config already there is kept as it is."""
    before = sorted(p.name for p in (ROOT / "data").iterdir())
    fresh = DataPaths(tmp_path / "demo")
    reset(fresh, preload=True, only=DEMO_AES)
    assert (fresh.config / "users.yaml").read_bytes() == (
        ROOT / "data" / "config" / "users.yaml"
    ).read_bytes()
    assert [p.name for p in fresh.inbox.iterdir()] == ["01_initial.csv"]
    (fresh.config / "users.yaml").write_text("users: []\n")  # a local change survives
    reset(fresh, only=DEMO_AES)
    assert (fresh.config / "users.yaml").read_text() == "users: []\n"
    assert sorted(p.name for p in (ROOT / "data").iterdir()) == before
