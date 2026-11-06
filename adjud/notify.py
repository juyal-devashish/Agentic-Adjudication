"""Notifications written to outbox/ (mvp-flow-1.md §10).

A notification never carries clinical content: only study, case id, recipient,
template name and a link. The recipient is a site (`site:<id>`) or a role
(`role:safety_specialist`).
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
}


@dataclass
class Outbox:
    paths: DataPaths
    study: str
    clock: Clock = field(default=utc_now)

    def queue(self, case_id: str | None, to: str, template: str) -> dict[str, Any]:
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
            "link": f"/review?case={case_id}" if case_id else None,
            "created_at": at,
        }
        name = f"{at.replace(':', '')}-{note['id'][:8]}-{template}.json"
        write_atomic(
            self.paths.outbox / name,
            json.dumps(note, indent=1).encode(),
            overwrite=False,
        )
        return note
