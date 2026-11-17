"""Delivery Attempts per Channel (plan P-54, ADR 0002): one Channel's tries never use
up or end another's, and lines written before Channels existed count as e-mail."""

from __future__ import annotations

import json

from adjud.delivery import EMAIL, SLACK, DeliveryLog, channels_from_env
from tests.test_mailer import FakeSmtp, mailer, pipe  # noqa: F401  (fixture)


def test_notify_channels_unset_means_as_before():
    assert channels_from_env({}) is None
    assert channels_from_env({"NOTIFY_CHANNELS": " "}) is None
    assert channels_from_env({"NOTIFY_CHANNELS": "outbox,smtp,slack"}) == {EMAIL, SLACK}
    assert channels_from_env({"NOTIFY_CHANNELS": "outbox, Teams"}) == set()


def test_slack_attempts_never_count_for_email(pipe):  # noqa: F811
    log = DeliveryLog(pipe.store, pipe.clock)
    note = log.notes()[0]
    for _ in range(3):
        log.record(note, SLACK, "system:slack", "failed", [], "timeout")
    log.record(log.notes()[1], SLACK, "system:slack", "sent", [], None)
    client = FakeSmtp()
    counts = mailer(pipe, client).deliver_pending()
    assert counts == {"sent": len(log.notes()), "failed": 0}  # every note, both included
    assert log.pending(SLACK) == log.notes()[2:]


def test_lines_without_a_channel_count_as_email(pipe):  # noqa: F811
    log = DeliveryLog(pipe.store, pipe.clock)
    note = log.notes()[0]
    log.outbox.mkdir(parents=True, exist_ok=True)
    log.path.write_text(json.dumps({"id": note["id"], "status": "sent", "to": []}) + "\n")
    assert log.attempts(EMAIL) == {note["id"]: ["sent"]}
    assert note not in log.pending(EMAIL) and note in log.pending(SLACK)
