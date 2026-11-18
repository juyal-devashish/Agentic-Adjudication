"""E-mail delivery of queued notifications (plan step 10, P-29). Bare minimum on purpose.

The outbox (`outbox/*.json`, written by `adjud.notify.Outbox`) stays the record of what
was queued. When SMTP is configured, the runner calls `Mailer.deliver_pending()` every
tick: each notification is e-mailed to the people behind its recipient (`site:101` → that
site's users, `role:safety_specialist` → everyone with that role), and the result is a Delivery
Attempt through the `email` Channel (`adjud.delivery`): a `notification.sent` or
`notification.failed` event plus one line in `outbox/delivery.jsonl`. A notification is
tried at most `MAX_ATTEMPTS` times, once per tick, so a broken server never causes a
storm of retries. Other Channels' attempts (e.g. Slack, P-54) never count here.

Configuration, from the environment only (never written to data/ or the repository):

    SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = TLS, other = plain, e.g. Mailpit 1025),
    SMTP_USER, SMTP_PASSWORD, SMTP_FROM, APP_BASE_URL (default http://localhost:8501),
    SMTP_REDIRECT_TO   demo: send every message to this one address (the presenter)

Messages carry no clinical content: case ID, task and link only.
"""

from __future__ import annotations

import os
import smtplib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Protocol

import yaml

from adjud.delivery import EMAIL, MAX_ATTEMPTS, DeliveryLog, directory, people
from adjud.events import Clock, utc_now
from adjud.store import FileCaseStore

ACTOR = "system:mailer"
__all__ = ["MAX_ATTEMPTS", "Mailer", "SmtpSettings", "compose", "connect", "recipients"]


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
    return [u for u in people(to, users) if u.get("email")]


def compose(
    note: dict[str, Any], user: dict[str, Any], s: SmtpSettings, organisation: str = ""
) -> EmailMessage:
    """A short letter: who it is for, the task, the case and a link. No clinical content."""
    msg = EmailMessage()
    case = note.get("case_id")
    msg["Subject"] = f"[{note['study']}] {note['subject']}" + (f": case {case}" if case else "")
    msg["From"] = s.sender
    msg["To"] = s.redirect_to or formataddr((user["name"], user["email"]))
    link = f"{s.base_url}{note['link']}" if note.get("link") else s.base_url
    zip_hint = note["template"] in ("documents_requested", "query_opened", "upload_rejected")
    lines = [
        f"Dear {user['name']},",
        "",
        f"Case {case}: {note['subject'][:1].lower()}{note['subject'][1:]}."
        if case
        else f"{note['subject']}.",
        "",
        f"Open the case: {link}" if case else f"Open the app: {link}",
        *(
            ["You can also put all documents for this case in one ZIP file in its upload folder."]
            if zip_hint
            else []
        ),
        "",
        "This message contains no patient or clinical information.",
        "",
        "Kind regards,",
        f"{organisation} Clinical Adjudication".strip(),
        "",
        "Demonstration with synthetic data only.",
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

    def __post_init__(self) -> None:
        self.log = DeliveryLog(self.store, self.clock)

    @property
    def delivery_log(self) -> Path:
        return self.log.path

    def attempts(self) -> dict[str, list[str]]:
        """Notification id -> e-mail statuses so far ("sent" / "failed")."""
        return self.log.attempts(EMAIL)

    def pending(self) -> list[dict[str, Any]]:
        return self.log.pending(EMAIL)

    def _organisation(self, study: str) -> str:
        """Who signs the e-mails: `organisation` in the study's study.yaml, if set."""
        path = self.config_dir / study / "study.yaml"
        return (
            (yaml.safe_load(path.read_text()) or {}).get("organisation", "")
            if (study and path.exists())
            else ""
        )

    def deliver_pending(self) -> dict[str, int]:
        notes = self.pending()
        counts = {"sent": 0, "failed": 0}
        if not notes:
            return counts
        users = directory(self.config_dir, notes[0].get("study", ""))
        organisation = self._organisation(notes[0].get("study", ""))
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
                        client.send_message(compose(n, u, self.settings, organisation))
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
        self.log.record(note, EMAIL, ACTOR, status, to, error)
