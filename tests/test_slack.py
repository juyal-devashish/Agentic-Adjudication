"""Slack notices as DMs (plan P-55, ADR 0003; delivery rules from P-54), with a fake
Slack client: nothing here reaches Slack. Same content rule as e-mail; a failure is a
recorded Delivery Attempt and never holds up e-mail or the workflow; only Notifications
queued after Slack was switched on are sent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web import SlackResponse

from adjud.delivery import EMAIL, MAX_ATTEMPTS, SLACK, Deliverer, DeliveryLog
from adjud.events import format_time
from adjud.notify import Outbox
from adjud.paths import DataPaths
from adjud.runner import Runner
from adjud.slack_app.core import Directory
from adjud.slack_app.dm import ACTOR, SlackDmChannel, SlackSettings
from tests.test_mailer import CONFIG, FakeSmtp, mailer, pipe  # noqa: F401  (fixture)
from tools.reset_demo import DEMO_AES, reset

TOKEN = "xoxb-test-secret-token"
SETTINGS = SlackSettings(bot_token=TOKEN)
PRESENTER = "U0PRESENTER"
ROOT = Path(__file__).resolve().parents[1]


def slack_error(error: str, status: int) -> SlackApiError:
    resp = SlackResponse(client=None, http_verb="POST", api_url="chat.postMessage",
                         req_args={}, data={"ok": False, "error": error}, headers={},
                         status_code=status)  # fmt: skip
    return SlackApiError(f"The request failed: {error}", resp)


class FakeClient:
    """Stands in for slack_sdk's WebClient: answers each post with the next reply."""

    def __init__(self, *replies):
        self.replies = list(replies) or ["ok"]
        self.posts: list[dict] = []

    def chat_postMessage(self, **kwargs):  # noqa: N802 (slack_sdk's name)
        self.posts.append(kwargs)
        reply = self.replies[0] if len(self.replies) == 1 else self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"ok": True}


def directory(pipe, presenter: str | None = PRESENTER) -> Directory:  # noqa: F811
    d = Directory(CONFIG, pipe.store.paths.root / "slack")
    if presenter:
        d.act_as(presenter, "dm.dana")
    return d


def deliverer(pipe, client, since=None, presenter=PRESENTER) -> Deliverer:  # noqa: F811
    channel = SlackDmChannel(directory(pipe, presenter), SETTINGS, client)
    return Deliverer(pipe.store, CONFIG, channel, ACTOR, since, pipe.clock)


def queue_two(pipe):  # noqa: F811
    out = Outbox(pipe.store.paths, "DEMO01", clock=pipe.clock)
    a = out.queue("DEMO01-0001", "user:adj.okafor", "consensus_to_sign", "adjudicator", "committee")
    b = out.queue("DEMO01-0001", "mailbox:pharmacovigilance", "outcome_for_pv")
    return a, b


def test_a_dm_names_the_case_task_and_person_and_opens_the_app(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    client = FakeClient()
    deliverer(pipe, client, since).deliver_pending()
    chair, pv = client.posts
    assert chair["channel"] == PRESENTER and chair["unfurl_links"] is False
    body = json.dumps(chair["blocks"])
    assert "[DEMO01] Case DEMO01-0001: a committee consensus needs your signature" in body
    assert "For: Dr. Daniel Okafor (Adjudicator, committee Chair)" in body
    button = chair["blocks"][1]["elements"][0]
    assert button["text"]["text"] == "Open in the app"
    assert button["url"].startswith("http://localhost:8501/committee?role=adjudicator&user=")
    assert "@" not in body and "Synthetic data only" in body
    assert "For: Pharmacovigilance team (mailbox)" in json.dumps(pv["blocks"])
    assert "Open in the app" not in json.dumps(pv["blocks"])  # a mailbox gets no link
    assert "related" not in json.dumps(pv["blocks"]).lower()


def test_people_sharing_one_slack_account_get_one_dm(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    Outbox(pipe.store.paths, "DEMO01", clock=pipe.clock).queue(
        "DEMO01-0001", "role:adjudicator", "committee_needed"
    )
    client = FakeClient()
    deliverer(pipe, client, since).deliver_pending()
    assert len(client.posts) == 1  # three adjudicators, all the presenter in the demo
    assert "Dr. Daniel Okafor, Dr. Elin Lindqvist, Dr. Marco Reyes (Adjudicator)" in json.dumps(
        client.posts[0]["blocks"])  # fmt: skip


def test_only_notifications_queued_after_switching_on_are_sent_once(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    client = FakeClient()
    assert deliverer(pipe, client, since).deliver_pending() == {"sent": 2, "failed": 0}
    assert deliverer(pipe, client, since).deliver_pending() == {"sent": 0, "failed": 0}
    assert len(client.posts) == 2  # the import's 11 older notifications were left alone
    sent = [e for e in pipe.store.events("DEMO01-0001") if e["type"] == "notification.sent"]
    assert {e["data"]["channel"] for e in sent} == {SLACK}
    assert {e["actor"] for e in sent} == {ACTOR}


def test_notices_wait_without_a_try_until_someone_acts_as(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    for _ in range(MAX_ATTEMPTS + 1):  # more ticks than tries: nothing is used up
        counts = deliverer(pipe, FakeClient(), since, presenter=None).deliver_pending()
        assert counts == {"sent": 0, "failed": 0}
    assert DeliveryLog(pipe.store, pipe.clock).attempts(SLACK) == {}
    client = FakeClient()
    assert deliverer(pipe, client, since).deliver_pending() == {"sent": 2, "failed": 0}


def test_a_retry_never_dms_someone_already_reached(pipe):  # noqa: F811
    """Real use: two people with their own Slack users; the second post fails once."""
    since = format_time(pipe.clock())
    d = directory(pipe)
    for uid, slack in (("adj.okafor", "U0OKAFOR"), ("adj.reyes", "U0REYES")):
        d.by_id[uid]["slack_user_id"] = slack
    Outbox(pipe.store.paths, "DEMO01", clock=pipe.clock).queue(
        "DEMO01-0001", "role:adjudicator", "committee_needed"
    )
    client = FakeClient("ok", slack_error("user_not_found", 404), "ok", "ok")
    channel = SlackDmChannel(d, SETTINGS, client)
    for _ in range(2):
        Deliverer(pipe.store, CONFIG, channel, ACTOR, since, pipe.clock).deliver_pending()
    # Okafor reached; the presenter (Lindqvist, no own Slack user) fails, so the next tick
    # retries: Okafor is not DMed again, the presenter and Reyes are.
    assert [p["channel"] for p in client.posts] == ["U0OKAFOR", PRESENTER, PRESENTER, "U0REYES"]


def test_the_last_account_to_choose_is_the_presenter(pipe):  # noqa: F811
    d = Directory(CONFIG, pipe.store.paths.root / "slack")
    d.act_as("U1", "dm.dana")
    d.act_as("U2", "ss.anna")
    d.act_as("U1", "site101.coord")
    assert d.slack_user_for("adj.reyes") == "U1"


def test_a_refused_post_is_recorded_retried_three_times_and_email_still_goes(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    client = FakeClient(slack_error("channel_not_found", 404))
    for _ in range(MAX_ATTEMPTS + 2):
        deliverer(pipe, client, since).deliver_pending()
    log = DeliveryLog(pipe.store, pipe.clock)
    assert all(len(v) == MAX_ATTEMPTS for v in log.attempts(SLACK).values())
    failed = [e for e in pipe.store.events("DEMO01-0001") if e["type"] == "notification.failed"]
    assert failed[0]["data"]["error"] == "Slack channel_not_found"
    assert mailer(pipe, FakeSmtp()).deliver_pending()["failed"] == 0
    assert all(v == ["sent"] for v in log.attempts(EMAIL).values())
    assert log.pending(SLACK, since) == []  # given up on, still in the outbox
    assert len(log.notes()) == 13


def test_when_slack_does_not_answer_the_rest_of_the_tick_is_not_tried(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    client = FakeClient(TimeoutError(), "ok")
    assert deliverer(pipe, client, since).deliver_pending() == {"sent": 0, "failed": 2}
    assert len(client.posts) == 1  # one 10-second wait per tick at most
    assert deliverer(pipe, client, since).deliver_pending() == {"sent": 2, "failed": 0}
    statuses = DeliveryLog(pipe.store, pipe.clock).attempts(SLACK)
    assert sorted(statuses.values()) == [["failed", "sent"], ["failed", "sent"]]


def test_rate_limited_is_a_failed_try_and_waits_for_the_next_tick(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    client = FakeClient(slack_error("ratelimited", 429))
    assert deliverer(pipe, client, since).deliver_pending() == {"sent": 0, "failed": 2}
    assert len(client.posts) == 1


def test_the_token_and_odd_errors_are_never_written_down(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    deliverer(pipe, FakeClient(slack_error("bad " + TOKEN, 400)), since).deliver_pending()
    deliverer(pipe, FakeClient(ValueError(TOKEN)), since).deliver_pending()
    written = (pipe.store.paths.outbox / "delivery.jsonl").read_text()
    written += "".join(json.dumps(e) for e in pipe.store.events("DEMO01-0001"))
    assert "secret" not in written and "xoxb" not in written
    assert '"error": "Slack error"' in written and '"error": "error: ValueError"' in written
    assert "secret" not in repr(SETTINGS)


def test_settings_need_a_bot_token():
    assert SlackSettings.from_env({}) is None
    s = SlackSettings.from_env({"SLACK_BOT_TOKEN": TOKEN, "APP_BASE_URL": "http://h:1/"})
    assert s is not None and s.base_url == "http://h:1"


@pytest.fixture
def data(tmp_path, monkeypatch) -> DataPaths:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    root = tmp_path / "data"
    root.mkdir()
    (root / "config").symlink_to(ROOT / "data" / "config")
    paths = DataPaths(root)
    reset(paths, only=DEMO_AES)
    return paths


def test_the_runner_switches_channels_from_notify_channels(data, monkeypatch):
    assert Runner(data).slack is None and Runner(data).mailer is None  # unset: as before
    monkeypatch.setenv("SMTP_HOST", "mailpit")
    assert Runner(data).mailer is not None and Runner(data).slack is None
    monkeypatch.setenv("NOTIFY_CHANNELS", "outbox,slack")  # listed, not configured: off
    assert Runner(data).slack is None and Runner(data).mailer is None
    monkeypatch.setenv("SLACK_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("NOTIFY_CHANNELS", "outbox,smtp,slack")
    r = Runner(data)
    assert r.mailer is not None and r.slack is not None


def test_a_slack_crash_never_reaches_the_email_or_the_runner(data, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("NOTIFY_CHANNELS", "slack")
    r = Runner(data)

    def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(r.slack, "deliver_pending", boom)
    r.send_slack()  # logged, not raised


def test_the_case_record_shows_slack_attempts(data, monkeypatch):
    from tests.test_ui_flow3 import as_user, tables

    monkeypatch.setenv("ADJUD_DATA_DIR", str(data.root))
    reset(data, preload=True, only=DEMO_AES)
    runner = Runner(data)
    runner.run_once()
    log = DeliveryLog(runner.pipeline.store)
    note = next(n for n in log.notes() if n.get("case_id") == "DEMO01-0001")
    log.record(note, SLACK, ACTOR, "failed", [], "Slack channel_not_found")
    at = as_user("ss.anna", "views/results.py", "DEMO01-0001")
    assert not at.exception, at.exception
    shown = tables(at)
    assert "Slack message failed: Slack channel_not_found" in shown
    table = next(d.value for d in at.dataframe if "Slack" in d.value.columns)
    assert list(table["Slack"]) == ["Failed"] and list(table["E-mail"]) == ["Not sent"]


def test_parity_is_clean_only_when_every_notification_was_sent_once(pipe):  # noqa: F811
    from tools.notify_parity import parity

    log = DeliveryLog(pipe.store, pipe.clock)
    assert not parity(log)["clean"]  # Slack never tried
    since = format_time(pipe.clock())
    queue_two(pipe)
    deliverer(pipe, FakeClient(), since).deliver_pending()
    mailer(pipe, FakeSmtp()).deliver_pending()
    report = parity(log)
    assert report["clean"], report["problems"]
    assert (report["notifications"], report["slack_sent"], report["email_sent"]) == (2, 2, 2)

    Outbox(pipe.store.paths, "DEMO01", clock=pipe.clock).queue("DEMO01-0001", "site:101",
                                                               "query_opened")  # fmt: skip
    deliverer(pipe, FakeClient(slack_error("internal_error", 500)), since).deliver_pending()
    report = parity(log)
    assert not report["clean"]
    assert any("posted 0 times" in p for p in report["problems"])
    assert any("internal_error" in p for p in report["problems"])


def test_a_restart_keeps_the_time_slack_was_switched_on(pipe):  # noqa: F811
    from adjud.runner import slack_since

    log = DeliveryLog(pipe.store, pipe.clock)
    first = slack_since(log, pipe.clock)  # nothing tried yet: now
    a, _ = queue_two(pipe)
    log.record(a, SLACK, ACTOR, "failed", [], "timeout")
    assert slack_since(log, pipe.clock) == a["created_at"] >= first


def test_each_dm_says_what_the_recipient_does_next(pipe):  # noqa: F811
    """P-57: a DM guides as well as notifies. Its Next: line is the recipient's Task on
    the case when the DM is sent, worded as on App Home; else nothing more to do."""
    import shutil

    from adjud.slack_app.core import Workspace

    shutil.copytree(CONFIG, pipe.store.paths.root / "config")
    shutil.copytree(
        ROOT / "data" / "flow1-testkit" / "edc_mock", pipe.store.paths.root / "edc_mock"
    )
    ws = Workspace(DataPaths(pipe.store.paths.root), clock=pipe.clock)
    since = format_time(pipe.clock())
    out = Outbox(pipe.store.paths, "DEMO01", clock=pipe.clock)
    out.queue("DEMO01-0001", "site:101", "documents_requested")
    out.queue("DEMO01-0001", "role:safety_specialist", "source_changed")
    client = FakeClient()
    channel = SlackDmChannel(directory(pipe), SETTINGS, client, next_step=ws.next_step)
    Deliverer(pipe.store, CONFIG, channel, ACTOR, since, pipe.clock).deliver_pending()
    site, ss = (json.dumps(p["blocks"]) for p in client.posts)
    assert "Next: Upload the documents" in site
    assert "Next: Nothing more for you on this case now." in ss
