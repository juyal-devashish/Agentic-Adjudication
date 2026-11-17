"""Slack on trial next to e-mail (plan P-54, ADR 0002), with a fake Slack: nothing here
reaches Slack. Same content rule as e-mail; a failure is a recorded Delivery Attempt and
never holds up e-mail or the workflow; only Notifications queued after Slack was
switched on are posted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adjud.delivery import EMAIL, MAX_ATTEMPTS, SLACK, Deliverer, DeliveryLog
from adjud.events import format_time
from adjud.notify import Outbox
from adjud.paths import DataPaths
from adjud.runner import Runner
from adjud.slack import ACTOR, SlackChannel, SlackSettings, message
from tests.test_mailer import CONFIG, FakeSmtp, mailer, pipe  # noqa: F401  (fixture)
from tools.reset_demo import DEMO_AES, reset

URL = "https://hooks.slack.test/services/T000/B000/secret-part"
SETTINGS = SlackSettings(webhook_url=URL)
ROOT = Path(__file__).resolve().parents[1]


class FakeSlack:
    """Stands in for the webhook: answers each post with the next reply, or raises it."""

    def __init__(self, *replies):
        self.replies = list(replies) or [(200, "ok")]
        self.posts: list[dict] = []

    def __call__(self, url, body, timeout):
        assert url == URL and timeout == 10
        self.posts.append(json.loads(body))
        reply = self.replies[0] if len(self.replies) == 1 else self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def deliverer(pipe, fake, since=None) -> Deliverer:  # noqa: F811
    return Deliverer(
        pipe.store, CONFIG, SlackChannel(SETTINGS, post=fake), ACTOR, since, pipe.clock
    )


def queue_two(pipe):  # noqa: F811
    out = Outbox(pipe.store.paths, "DEMO01", clock=pipe.clock)
    a = out.queue("DEMO01-0001", "user:adj.okafor", "consensus_to_sign", "adjudicator", "committee")
    b = out.queue("DEMO01-0001", "mailbox:pharmacovigilance", "outcome_for_pv")
    return a, b


def test_the_message_names_the_case_task_and_person_and_nothing_clinical(pipe):  # noqa: F811
    chair, pv = queue_two(pipe)
    users = [{"id": "adj.okafor", "name": "Dr. Daniel Okafor", "role": "adjudicator",
              "email": "daniel.okafor@techobserver.com"}]  # fmt: skip
    m = message(chair, users, "http://localhost:8501")
    assert m["unfurl_links"] is False and m["unfurl_media"] is False
    text = m["text"]
    assert "[DEMO01] Case DEMO01-0001: a committee consensus needs your signature" in text
    assert "For: Dr. Daniel Okafor (Adjudicator)" in text
    assert "<http://localhost:8501/committee?role=adjudicator&user=adj.okafor" in text
    assert "@" not in text and "Synthetic data only" in text
    m = message(pv, [{"id": "mailbox:pharmacovigilance", "name": "PV"}], "http://x")
    assert "For: PV (mailbox)" in m["text"] and "Open the case" not in m["text"]
    assert "related" not in m["text"].lower()


def test_only_notifications_queued_after_switching_on_are_posted_once(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    fake = FakeSlack()
    assert deliverer(pipe, fake, since).deliver_pending() == {"sent": 2, "failed": 0}
    assert deliverer(pipe, fake, since).deliver_pending() == {"sent": 0, "failed": 0}
    assert len(fake.posts) == 2  # the import's 11 older notifications were left alone
    sent = [e for e in pipe.store.events("DEMO01-0001") if e["type"] == "notification.sent"]
    assert {e["data"]["channel"] for e in sent} == {SLACK}
    assert {e["actor"] for e in sent} == {ACTOR}


def test_a_refused_post_is_recorded_retried_three_times_and_email_still_goes(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    fake = FakeSlack((404, "no_service"))
    for _ in range(MAX_ATTEMPTS + 2):
        deliverer(pipe, fake, since).deliver_pending()
    log = DeliveryLog(pipe.store, pipe.clock)
    assert all(len(v) == MAX_ATTEMPTS for v in log.attempts(SLACK).values())
    failed = [e for e in pipe.store.events("DEMO01-0001") if e["type"] == "notification.failed"]
    assert failed[0]["data"]["error"] == "HTTP 404 no_service"
    assert mailer(pipe, FakeSmtp()).deliver_pending()["failed"] == 0
    assert all(v == ["sent"] for v in log.attempts(EMAIL).values())
    assert log.pending(SLACK, since) == []  # given up on, still in the outbox
    assert len(log.notes()) == 13


def test_when_slack_does_not_answer_the_rest_of_the_tick_is_not_tried(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    fake = FakeSlack(TimeoutError(), (200, "ok"))
    assert deliverer(pipe, fake, since).deliver_pending() == {"sent": 0, "failed": 2}
    assert len(fake.posts) == 1  # one 10-second wait per tick at most
    assert deliverer(pipe, fake, since).deliver_pending() == {"sent": 2, "failed": 0}
    statuses = DeliveryLog(pipe.store, pipe.clock).attempts(SLACK)
    assert sorted(statuses.values()) == [["failed", "sent"], ["failed", "sent"]]


def test_the_webhook_url_is_never_written_down(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    deliverer(pipe, FakeSlack((403, "invalid_token " + URL)), since).deliver_pending()
    written = (pipe.store.paths.outbox / "delivery.jsonl").read_text()
    written += "".join(json.dumps(e) for e in pipe.store.events("DEMO01-0001"))
    assert "secret" not in written and "hooks" not in written
    assert '"error": "HTTP 403 invalid_token"' in written
    assert "secret-part" not in repr(SETTINGS)


def test_settings_need_an_https_webhook():
    assert SlackSettings.from_env({}) is None
    assert SlackSettings.from_env({"SLACK_WEBHOOK_URL": "file:///etc/passwd"}) is None
    s = SlackSettings.from_env({"SLACK_WEBHOOK_URL": URL, "APP_BASE_URL": "http://h:1/"})
    assert s is not None and s.base_url == "http://h:1"


@pytest.fixture
def data(tmp_path, monkeypatch) -> DataPaths:
    for var in ("NOTIFY_CHANNELS", "SMTP_HOST", "SLACK_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)
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
    monkeypatch.setenv("SLACK_WEBHOOK_URL", URL)
    monkeypatch.setenv("NOTIFY_CHANNELS", "outbox,smtp,slack")
    r = Runner(data)
    assert r.mailer is not None and r.slack is not None


def test_a_slack_crash_never_reaches_the_email_or_the_runner(data, monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", URL)
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
    log.record(note, SLACK, ACTOR, "failed", [], "HTTP 404 no_service")
    at = as_user("ss.anna", "views/results.py", "DEMO01-0001")
    assert not at.exception, at.exception
    shown = tables(at)
    assert "Slack message failed: HTTP 404 no_service" in shown
    table = next(d.value for d in at.dataframe if "Slack" in d.value.columns)
    assert list(table["Slack"]) == ["Failed"] and list(table["E-mail"]) == ["Not sent"]


def test_parity_is_clean_only_when_every_notification_was_posted_once(pipe):  # noqa: F811
    from tools.notify_parity import parity

    log = DeliveryLog(pipe.store, pipe.clock)
    assert not parity(log)["clean"]  # Slack never tried
    since = format_time(pipe.clock())
    queue_two(pipe)
    deliverer(pipe, FakeSlack(), since).deliver_pending()
    mailer(pipe, FakeSmtp()).deliver_pending()
    report = parity(log)
    assert report["clean"], report["problems"]
    assert (report["notifications"], report["slack_sent"], report["email_sent"]) == (2, 2, 2)

    Outbox(pipe.store.paths, "DEMO01", clock=pipe.clock).queue("DEMO01-0001", "site:101",
                                                               "query_opened")  # fmt: skip
    deliverer(pipe, FakeSlack((500, "")), since).deliver_pending()
    report = parity(log)
    assert not report["clean"]
    assert any("posted 0 times" in p for p in report["problems"])
    assert any("HTTP 500" in p for p in report["problems"])


def test_an_unexpected_error_is_a_recorded_attempt_named_by_type_only(pipe):  # noqa: F811
    since = format_time(pipe.clock())
    queue_two(pipe)
    counts = deliverer(pipe, FakeSlack(ValueError(URL)), since).deliver_pending()
    assert counts == {"sent": 0, "failed": 2}
    written = (pipe.store.paths.outbox / "delivery.jsonl").read_text()
    assert "error: ValueError" in written and "secret" not in written


def test_a_restart_keeps_the_time_slack_was_switched_on(pipe):  # noqa: F811
    from adjud.runner import slack_since

    log = DeliveryLog(pipe.store, pipe.clock)
    first = slack_since(log, pipe.clock)  # nothing tried yet: now
    a, _ = queue_two(pipe)
    log.record(a, SLACK, ACTOR, "failed", [], "timeout")
    assert slack_since(log, pipe.clock) == a["created_at"] >= first
