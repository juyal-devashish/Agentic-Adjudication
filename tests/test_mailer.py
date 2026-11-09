"""E-mail delivery of notifications (plan step 10) with a fake SMTP client."""

from __future__ import annotations

import smtplib
from pathlib import Path

import pytest

from adjud.intake import IntakePipeline
from adjud.integrity import verify_all
from adjud.mailer import MAX_ATTEMPTS, Mailer, SmtpSettings, recipients
from adjud.notify import link

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
CONFIG = ROOT / "data" / "config"
SETTINGS = SmtpSettings(host="smtp.test", port=1025, sender="cec@example.com")


class FakeSmtp:
    def __init__(self, fail: bool = False):
        self.sent = []
        self.fail = fail

    def send_message(self, msg):
        if self.fail:
            raise smtplib.SMTPRecipientsRefused({})
        self.sent.append(msg)

    def quit(self):
        pass


@pytest.fixture
def pipe(tmp_path, clock) -> IntakePipeline:
    pipe = IntakePipeline(tmp_path / "data", CONFIG, KIT / "edc_mock", clock=clock)
    pipe.import_file(KIT / "inbox_scenarios" / "01_initial.csv")
    return pipe


def mailer(pipe, client, settings=SETTINGS) -> Mailer:
    return Mailer(pipe.store, CONFIG, settings, connect=lambda s: client, clock=pipe.clock)


def test_settings_absent_without_smtp_host():
    assert SmtpSettings.from_env({}) is None
    s = SmtpSettings.from_env({"SMTP_HOST": "smtp.office365.com", "SMTP_USER": "me@x.org"})
    assert (s.port, s.sender, s.base_url) == (587, "me@x.org", "http://localhost:8501")


def test_links_land_each_recipient_on_its_page():
    assert link("DEMO01-0001", "site:101") == "/documents?role=site&case=DEMO01-0001"
    assert link("DEMO01-0001", "role:safety_specialist") == (
        "/record?role=safety_specialist&case=DEMO01-0001"
    )


def test_site_notification_goes_to_that_sites_users_only():
    import yaml

    users = yaml.safe_load((CONFIG / "users.yaml").read_text())["users"]
    assert [u["id"] for u in recipients("site:101", users)] == ["site101.coord"]
    assert [u["id"] for u in recipients("role:safety_specialist", users)] == ["ss.anna"]


def test_every_new_case_mails_its_site_once_without_clinical_content(pipe):
    client = FakeSmtp()
    assert mailer(pipe, client).deliver_pending()["sent"] == 11
    assert mailer(pipe, client).deliver_pending() == {"sent": 0, "failed": 0}  # not twice
    assert len(client.sent) == 11
    msg = client.sent[0]
    body = msg.get_content()
    assert msg["To"] == "site101@example.com"
    assert "http://localhost:8501/documents?role=site&case=DEMO01-0001" in body
    snap = pipe.store.get_json("DEMO01-0001", "source/ae_snapshot_v1.json")
    for clinical in (snap["ae_term"], snap["onset_date"], snap["subject_id"]):
        assert clinical not in body and clinical not in msg["Subject"]
    types = [e["type"] for e in pipe.store.events("DEMO01-0001")]
    assert types.count("notification.sent") == 1
    assert all(r.ok for r in verify_all(pipe.paths))


def test_redirect_sends_everything_to_the_presenter(pipe):
    client = FakeSmtp()
    s = SmtpSettings(host="h", port=1025, sender="me@x.org", redirect_to="presenter@x.org")
    mailer(pipe, client, s).deliver_pending()
    assert {m["To"] for m in client.sent} == {"presenter@x.org"}
    assert "For: Site 101 Coordinator" in client.sent[0].get_content()


def test_failures_are_logged_and_retried_at_most_three_times(pipe):
    client = FakeSmtp(fail=True)
    for _ in range(MAX_ATTEMPTS + 2):
        mailer(pipe, client).deliver_pending()
    events = [e for e in pipe.store.events("DEMO01-0001") if e["type"] == "notification.failed"]
    assert len(events) == MAX_ATTEMPTS
    assert events[0]["data"]["error"] == "SMTPRecipientsRefused"


def test_server_down_marks_each_notification_failed_once_per_tick(pipe):
    def down(_s):
        raise ConnectionRefusedError

    m = Mailer(pipe.store, CONFIG, SETTINGS, connect=down, clock=pipe.clock)
    assert m.deliver_pending()["failed"] == 11
    assert m.attempts()[next(iter(m.attempts()))] == ["failed"]
