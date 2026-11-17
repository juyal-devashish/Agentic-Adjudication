"""Notifications written to outbox/.

A notification never carries clinical content: only study, case id, recipient,
template name and a link. The recipient is a site (`site:<id>`), a role
(`role:safety_specialist`), one person (`user:<id>`, e.g. an assigned adjudicator) or a
mailbox from study.yaml (`mailbox:pharmacovigilance`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from adjud.events import Clock, format_time, utc_now
from adjud.paths import DataPaths
from adjud.store import write_atomic

TEMPLATES = {
    "documents_requested": "Documents requested for an adjudication case",
    "processing_failed": "A document could not be processed",
    "ready_for_review": "Redacted documents are ready for your review",
    "submission_received": "Submission received",
    "documents_received": "Documents received, ready for QC",
    "source_changed": "The source AE changed after case creation",
    "possible_duplicate": "A new case may duplicate an existing one",
    "case_withdrawn": "Case withdrawn",
    "qc_review_needed": "A quality check needs your review",
    "query_opened": "A query about your documents needs an answer",
    "narrative_review_needed": "A narrative needs your review",
    "ready_for_adjudication": "Case ready for adjudication",
    "upload_rejected": "An uploaded ZIP file could not be used",
    # Flows 3 and 4
    "adjudication_assigned": "An adjudication review is assigned to you",
    "adj3_assigned": "Disputed fields of an adjudication need your review",
    "more_data_requested": "More information was requested for an adjudication",
    "committee_needed": "A case needs the adjudication committee",
    "consensus_to_sign": "A committee consensus needs your signature",
    "outcome_for_pv": "An adjudication outcome needs pharmacovigilance attention",
    "adjudication_complete": "Adjudication complete",
    "readjudication_started": "Re-adjudication started",
}

# Where a person lands from an e-mail, by role (pages registered in ui/app.py).
ROLE_LANDING = {"adjudicator": "adjudicate", "cec_coordinator": "record"}


def link(
    case_id: str | None, to: str, role: str | None = None, page: str | None = None
) -> str | None:
    """Deep link into the UI for the recipient: a site lands on the case's documents, a
    role on the case record, one person (with their `role`) on their own page for the case
    (ui/app.py reads role, user and case from the query string). A mailbox gets no link."""
    if not case_id or to.startswith("mailbox:"):
        return None
    if to.startswith("user:") and role:
        page = page or ROLE_LANDING.get(role, "record")
        return f"/{page}?role={role}&user={to.removeprefix('user:')}&case={case_id}"
    if to.startswith("site:"):
        return f"/documents?role=site&case={case_id}"
    return f"/record?role={to.removeprefix('role:')}&case={case_id}"


@dataclass
class Outbox:
    paths: DataPaths
    study: str
    clock: Clock = field(default=utc_now)

    def queue(
        self,
        case_id: str | None,
        to: str,
        template: str,
        role: str | None = None,
        page: str | None = None,
    ) -> dict[str, Any]:
        if template not in TEMPLATES:
            raise ValueError(f"unknown notification template {template!r}")
        at = format_time(self.clock())
        note = {
            "id": uuid.uuid4().hex,
            "study": self.study,
            "case_id": case_id,
            "to": to,
            "template": template,
            "subject": TEMPLATES[template],
            "link": link(case_id, to, role, page),
            "created_at": at,
        }
        name = f"{at.replace(':', '')}-{note['id'][:8]}-{template}.json"
        write_atomic(
            self.paths.outbox / name,
            json.dumps(note, indent=1).encode(),
            overwrite=False,
        )
        return note
