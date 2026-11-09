"""E-mail delivery of queued notifications (plan step 10, P-29). Bare minimum on purpose.

The outbox (`outbox/*.json`, written by `adjud.notify.Outbox`) stays the record of what
was queued. When SMTP is configured, the runner calls `Mailer.deliver_pending()` every
tick: each notification is e-mailed to the people behind its recipient (`site:101` → that
site's users, `role:safety_specialist` → everyone with that role), and the result goes to
the case log as `notification.sent` or `notification.failed`, plus one line in
`outbox/delivery.jsonl` (append-only). A notification is tried at most `MAX_ATTEMPTS`
times, once per tick, so a broken server never causes a storm of retries.

Configuration, from the environment only (never written to data/ or the repository):

    SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = TLS, other = plain, e.g. Mailpit 1025),
    SMTP_USER, SMTP_PASSWORD, SMTP_FROM, APP_BASE_URL (default http://localhost:8501),
    SMTP_REDIRECT_TO   demo: send every message to this one address (the presenter)

Messages carry no clinical content: case ID, task and link only.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

import yaml

from adjud.events import Clock, EventDraft, format_time, utc_now
from adjud.store import FileCaseStore

log = logging.getLogger("adjud.mailer")
ACTOR = "system:mailer"
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    sender: str
    user: str | None = None
    password: str | None = None
    base_url: str = "http://localhost:8501"
    redirect_to: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SmtpSettings | None:
        """None when SMTP_HOST is not set: notifications then stay in the outbox only."""
        env = os.environ if env is None else env
        host = env.get("SMTP_HOST", "").strip()
        if not host:
            return None
        return cls(
            host=host,
            port=int(env.get("SMTP_PORT") or 587),
            sender=env.get("SMTP_FROM") or env.get("SMTP_USER") or "adjudication@localhost",
            user=env.get("SMTP_USER") or None,
            password=env.get("SMTP_PASSWORD") or None,
            base_url=(env.get("APP_BASE_URL") or "http://localhost:8501").rstrip("/"),
            redirect_to=env.get("SMTP_REDIRECT_TO") or None,
        )


class SmtpClient(Protocol):
    def send_message(self, msg: EmailMessage) -> Any: ...
    def quit(self) -> Any: ...


def connect(s: SmtpSettings) -> SmtpClient:
    """Open an SMTP session: implicit TLS on 465, STARTTLS on 587, plain otherwise."""
    if s.port == 465:
        client: smtplib.SMTP = smtplib.SMTP_SSL(s.host, s.port, timeout=20)
    else:
        client = smtplib.SMTP(s.host, s.port, timeout=20)
        if s.port == 587:
            client.starttls()
    if s.user and s.password:
        client.login(s.user, s.password)
    return client


def recipients(to: str, users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The users behind a notification recipient, who have an e-mail address."""
    kind, _, value = to.partition(":")
    if kind == "site":
        found = [u for u in users if u.get("role") == "site" and str(u.get("site")) == value]
    elif kind == "role":
        found = [u for u in users if u.get("role") == value]
    else:
        found = []
    return [u for u in found if u.get("email")]


def compose(note: dict[str, Any], user: dict[str, Any], s: SmtpSettings) -> EmailMessage:
    msg = EmailMessage()
    case = note.get("case_id") or "-"
    msg["Subject"] = f"[{note['study']}] {note['subject']}: case {case}"
    msg["From"] = s.sender
    msg["To"] = s.redirect_to or user["email"]
    link = f"{s.base_url}{note['link']}" if note.get("link") else s.base_url
    lines = [
        f"For: {user['name']}",
        f"Case: {case}",
        f"Task: {note['subject']}",
        f"Open: {link}",
        "",
        "Synthetic demo data. This message carries no clinical content.",
    ]
    msg.set_content("\n".join(lines))
    return msg


@dataclass
class Mailer:
    store: FileCaseStore
    config_dir: Path
    settings: SmtpSettings
    connect: Callable[[SmtpSettings], SmtpClient] = connect
    clock: Clock = field(default=utc_now)

    @property
    def outbox(self) -> Path:
        return self.store.paths.outbox

    @property
    def delivery_log(self) -> Path:
        return self.outbox / "delivery.jsonl"

    def attempts(self) -> dict[str, list[str]]:
        """Notification id -> statuses so far ("sent" / "failed"), from the delivery log."""
        out: dict[str, list[str]] = {}
        if self.delivery_log.exists():
            for line in self.delivery_log.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    out.setdefault(d["id"], []).append(d["status"])
        return out

    def pending(self) -> list[dict[str, Any]]:
        done = self.attempts()
        notes = [json.loads(p.read_text()) for p in sorted(self.outbox.glob("*.json"))]
        return [
            n
            for n in notes
            if "sent" not in done.get(n["id"], []) and len(done.get(n["id"], [])) < MAX_ATTEMPTS
        ]

    def deliver_pending(self) -> dict[str, int]:
        notes = self.pending()
        counts = {"sent": 0, "failed": 0}
        if not notes:
            return counts
        users = yaml.safe_load((self.config_dir / "users.yaml").read_text())["users"]
        try:
            client = self.connect(self.settings)
        except (OSError, smtplib.SMTPException) as exc:  # server down: every note failed once
            for n in notes:
                self._record(n, "failed", [], f"cannot connect: {type(exc).__name__}")
            counts["failed"] = len(notes)
            return counts
        try:
            for n in notes:
                people = recipients(n["to"], users)
                if not people:
                    self._record(n, "failed", [], "no recipient with an e-mail address")
                    counts["failed"] += 1
                    continue
                try:
                    for u in people:
                        client.send_message(compose(n, u, self.settings))
                except (OSError, smtplib.SMTPException) as exc:
                    self._record(n, "failed", [u["id"] for u in people], type(exc).__name__)
                    counts["failed"] += 1
                else:
                    self._record(n, "sent", [u["id"] for u in people], None)
                    counts["sent"] += 1
        finally:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                pass
        return counts

    def _record(self, note: dict[str, Any], status: str, to: list[str], error: str | None) -> None:
        line = {"id": note["id"], "status": status, "to": to, "at": format_time(self.clock())}
        if error:
            line["error"] = error
        self.outbox.mkdir(parents=True, exist_ok=True)
        with self.delivery_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        if note.get("case_id"):
            data = {"id": note["id"], "to": note["to"], "users": to}
            if error:
                data["error"] = error
            self.store.append_event(
                note["case_id"],
                EventDraft(type=f"notification.{status}", actor=ACTOR, data=data),
            )
        if status == "failed":
            log.warning("e-mail for notification %s failed: %s", note["id"], error)
