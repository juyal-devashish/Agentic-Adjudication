"""Delivery Attempts per Channel (plan P-54, ADR 0002).

The outbox (`outbox/*.json`, written by `adjud.notify.Outbox`) is the record of every
Notification. A Channel delivers them to people: e-mail (`adjud.mailer`), Slack on trial
(`adjud.slack`), Teams later. Each try at one Notification through one Channel is a
Delivery Attempt, one line in `outbox/delivery.jsonl` (append-only) and, for a case, a
`notification.sent` / `notification.failed` event, both carrying the Channel's name.
Lines written before Channels existed have no `channel` and count as e-mail.

Attempts are counted per Notification *and* Channel, so one Channel never uses up or
ends another's tries. A Notification is tried at most `MAX_ATTEMPTS` times per Channel,
once per runner tick.

`NOTIFY_CHANNELS` (environment) lists the Channels to deliver through, e.g.
`smtp,slack`. Unset: as before P-54, e-mail when SMTP_HOST is set. `outbox` may be
listed; it changes nothing, as the outbox is always written.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from adjud.events import Clock, EventDraft, format_time, utc_now
from adjud.store import FileCaseStore

log = logging.getLogger("adjud.delivery")
MAX_ATTEMPTS = 3
EMAIL = "email"
SLACK = "slack"
KNOWN_CHANNELS = {"outbox", "smtp", "email", SLACK}


def channels_from_env(env: Mapping[str, str] | None = None) -> set[str] | None:
    """The Channels named in NOTIFY_CHANNELS (`smtp` and `email` both mean e-mail), or
    None when it is unset: then behaviour is as before P-54."""
    env = os.environ if env is None else env
    raw = env.get("NOTIFY_CHANNELS", "").strip()
    if not raw:
        return None
    names = {n.strip().lower() for n in raw.split(",") if n.strip()}
    for unknown in sorted(names - KNOWN_CHANNELS):
        log.warning("NOTIFY_CHANNELS: unknown channel %r ignored", unknown)
    return {EMAIL if n == "smtp" else n for n in names & KNOWN_CHANNELS} - {"outbox"}


def directory(config_dir: Path, study: str) -> list[dict[str, Any]]:
    """Everyone a Notification can go to: the users, plus mailboxes from study.yaml that
    are not users of the app, e.g. pharmacovigilance (P-50)."""
    users = yaml.safe_load((config_dir / "users.yaml").read_text())["users"]
    path = config_dir / study / "study.yaml"
    cfg = (yaml.safe_load(path.read_text()) or {}) if (study and path.exists()) else {}
    pv = (cfg.get("adjudication") or {}).get("pharmacovigilance")
    return users + ([{"id": "mailbox:pharmacovigilance", **pv}] if pv else [])


def people(to: str, users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The people behind a recipient: `site:101` → that site's users, `role:x` → everyone
    with that role, `user:<id>` → that user, `mailbox:<name>` → that mailbox."""
    kind, _, value = to.partition(":")
    if kind == "site":
        return [u for u in users if u.get("role") == "site" and str(u.get("site")) == value]
    if kind == "role":
        return [u for u in users if u.get("role") == value]
    if kind in ("user", "mailbox"):
        return [u for u in users if u.get("id") == to or u.get("id") == value]
    return []


class ChannelError(Exception):
    """A Delivery Attempt failed. The message is the recorded reason: short and safe
    (e.g. "HTTP 404", "timeout"), never a secret, an address or clinical content."""

    def __init__(self, reason: str, *, unreachable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.unreachable = unreachable  # the service is down: skip the rest of this tick


class Channel(Protocol):
    """A route that delivers one Notification to the people behind its recipient."""

    name: str

    def send(self, note: dict[str, Any], people: list[dict[str, Any]]) -> None:
        """Deliver, or raise ChannelError with the reason."""
        ...


@dataclass
class DeliveryLog:
    store: FileCaseStore
    clock: Clock = field(default=utc_now)

    @property
    def outbox(self) -> Path:
        return self.store.paths.outbox

    @property
    def path(self) -> Path:
        return self.outbox / "delivery.jsonl"

    def lines(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                d.setdefault("channel", EMAIL)
                out.append(d)
        return out

    def attempts(self, channel: str) -> dict[str, list[str]]:
        """Notification id -> statuses so far ("sent" / "failed") through `channel`."""
        out: dict[str, list[str]] = {}
        for d in self.lines():
            if d["channel"] == channel:
                out.setdefault(d["id"], []).append(d["status"])
        return out

    def notes(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text()) for p in sorted(self.outbox.glob("*.json"))]

    def pending(self, channel: str, since: str | None = None) -> list[dict[str, Any]]:
        """Notifications still to try through `channel`: not sent, fewer than MAX_ATTEMPTS
        tries. With `since`, a Notification created earlier is left alone unless this
        Channel has already tried it (so switching a Channel on posts no backlog)."""
        done = self.attempts(channel)
        return [
            n
            for n in self.notes()
            if "sent" not in done.get(n["id"], [])
            and len(done.get(n["id"], [])) < MAX_ATTEMPTS
            and (since is None or n["created_at"] >= since or n["id"] in done)
        ]

    def record(
        self,
        note: dict[str, Any],
        channel: str,
        actor: str,
        status: str,
        to: list[str],
        error: str | None,
    ) -> None:
        line = {
            "id": note["id"],
            "channel": channel,
            "status": status,
            "to": to,
            "at": format_time(self.clock()),
        }
        if error:
            line["error"] = error
        self.outbox.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        if note.get("case_id"):
            data = {"id": note["id"], "channel": channel, "to": note["to"], "users": to}
            if error:
                data["error"] = error
            self.store.append_event(
                note["case_id"],
                EventDraft(type=f"notification.{status}", actor=actor, data=data),
            )
        if status == "failed":
            log.warning("%s for notification %s failed: %s", channel, note["id"], error)


@dataclass
class Deliverer:
    """Delivers pending Notifications through one Channel, one Notification at a time.
    Used by every Channel except e-mail, which keeps its own loop because one SMTP
    session serves a whole tick."""

    store: FileCaseStore
    config_dir: Path
    channel: Channel
    actor: str
    since: str | None = None  # Notifications created before the Channel was switched on
    clock: Clock = field(default=utc_now)

    def __post_init__(self) -> None:
        self.log = DeliveryLog(self.store, self.clock)

    def deliver_pending(self) -> dict[str, int]:
        notes = self.log.pending(self.channel.name, self.since)
        counts = {"sent": 0, "failed": 0}
        if not notes:
            return counts
        users = directory(self.config_dir, notes[0].get("study", ""))
        down: str | None = None
        for n in notes:
            who = people(n["to"], users)
            ids = [u["id"] for u in who]
            if down is not None:  # the service did not answer: one failed try each
                self.log.record(n, self.channel.name, self.actor, "failed", ids, down)
                counts["failed"] += 1
                continue
            if not who:
                self.log.record(n, self.channel.name, self.actor, "failed", [], "no recipient")
                counts["failed"] += 1
                continue
            try:
                self.channel.send(n, who)
            except ChannelError as exc:
                self.log.record(n, self.channel.name, self.actor, "failed", ids, exc.reason)
                counts["failed"] += 1
                if exc.unreachable:
                    down = exc.reason
            else:
                self.log.record(n, self.channel.name, self.actor, "sent", ids, None)
                counts["sent"] += 1
        return counts
