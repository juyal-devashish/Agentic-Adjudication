"""Case states for Flows 1 to 4, the transition table and replay.

The current state of a case is never stored as truth: it is computed by replaying the
case's events through this table. case.json is only a cache of that result.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from adjud.events import canonical_json


class State(StrEnum):
    EVENT_CREATED = "EVENT_CREATED"
    AWAITING_DOCUMENTS = "AWAITING_DOCUMENTS"
    DEIDENTIFYING = "DEIDENTIFYING"
    AWAITING_SITE_CONFIRMATION = "AWAITING_SITE_CONFIRMATION"
    SUBMITTED = "SUBMITTED"
    # Flow 2 (plan step 13): agents act, gates decide, people handle the *_REVIEW states.
    QC_CHECKS = "QC_CHECKS"
    QC_REVIEW = "QC_REVIEW"
    QUERY_OPEN = "QUERY_OPEN"
    NARRATIVE = "NARRATIVE"
    NARRATIVE_REVIEW = "NARRATIVE_REVIEW"
    READY_FOR_ADJUDICATION = "READY_FOR_ADJUDICATION"
    # Flows 3 and 4 (plan P-41 onwards, spec §5.2): people adjudicate, code compares.
    ADJ_REVIEW = "ADJ_REVIEW"
    ADJ3_REVIEW = "ADJ3_REVIEW"
    CEC_MEETING = "CEC_MEETING"
    MORE_DATA = "MORE_DATA"
    OUTCOME = "OUTCOME"
    EXPORT = "EXPORT"
    CLOSED = "CLOSED"
    WITHDRAWN = "WITHDRAWN"


OPEN_STATES = frozenset(
    {
        State.EVENT_CREATED,
        State.AWAITING_DOCUMENTS,
        State.DEIDENTIFYING,
        State.AWAITING_SITE_CONFIRMATION,
    }
)
ALL_STATES = frozenset(State)

# States in which the runner (not a person) has the next action.
AUTOMATIC_STATES = frozenset(
    {
        State.EVENT_CREATED,
        State.DEIDENTIFYING,
        State.SUBMITTED,
        State.QC_CHECKS,
        State.NARRATIVE,
        State.READY_FOR_ADJUDICATION,
        State.MORE_DATA,
        State.OUTCOME,
        State.EXPORT,
    }
)

SAME = "SAME"  # event is recorded but does not change state

# event type -> {allowed from-state: to-state or SAME}. `None` as from-state = new case.
TRANSITIONS: dict[str, dict[State | None, State | str]] = {
    "case.created": {None: State.EVENT_CREATED},
    "edc.related_data_pulled": {State.EVENT_CREATED: State.AWAITING_DOCUMENTS},
    "documents.uploaded": {
        State.AWAITING_DOCUMENTS: State.DEIDENTIFYING,
        State.AWAITING_SITE_CONFIRMATION: State.DEIDENTIFYING,
        State.DEIDENTIFYING: SAME,
        State.QUERY_OPEN: State.DEIDENTIFYING,  # the site answers a QC query
    },
    "deid.started": {State.DEIDENTIFYING: SAME},
    "deid.file_processed": {State.DEIDENTIFYING: SAME},
    "deid.completed": {State.DEIDENTIFYING: State.AWAITING_SITE_CONFIRMATION},
    "deid.failed": {State.DEIDENTIFYING: State.AWAITING_DOCUMENTS},
    "redactions.changed": {State.AWAITING_SITE_CONFIRMATION: State.DEIDENTIFYING},
    "document.labelled": {State.AWAITING_SITE_CONFIRMATION: SAME},  # site corrects a type
    "document.deleted": {
        State.AWAITING_DOCUMENTS: SAME,
        State.AWAITING_SITE_CONFIRMATION: SAME,
        State.QUERY_OPEN: SAME,
    },
    "submission.signed": {State.AWAITING_SITE_CONFIRMATION: State.SUBMITTED},
    "handoff.flow2_ready": {State.SUBMITTED: SAME},
    # --- Flow 2 ---
    "qc.started": {State.SUBMITTED: State.QC_CHECKS},  # reads the Flow 2 manifest only
    "qc.checks_completed": {State.QC_CHECKS: SAME},
    "qc.passed": {State.QC_CHECKS: State.NARRATIVE},  # by agent:qc when its gate passes
    "qc.review_needed": {State.QC_CHECKS: State.QC_REVIEW},
    "qc.signed_pass": {State.QC_REVIEW: State.NARRATIVE},  # before P-39; still replays
    # P-39: one signature covers the QC pass and the edited narrative
    "quality_check.signed": {State.QC_REVIEW: State.READY_FOR_ADJUDICATION},
    "query.opened": {
        State.QC_CHECKS: State.QUERY_OPEN,
        State.QC_REVIEW: State.QUERY_OPEN,
        State.MORE_DATA: State.QUERY_OPEN,  # more data for the adjudicators (P-47)
    },
    # Built in the QC round since P-39, after the pass before it (cases on disk replay)
    "dossier.built": {State.QC_CHECKS: SAME, State.NARRATIVE: SAME},
    "narrative.drafted": {State.QC_CHECKS: SAME, State.NARRATIVE: SAME},
    "narrative.approved": {State.NARRATIVE: State.READY_FOR_ADJUDICATION},  # agent gate
    "narrative.review_needed": {State.NARRATIVE: State.NARRATIVE_REVIEW},
    "narrative.signed": {State.NARRATIVE_REVIEW: State.READY_FOR_ADJUDICATION},
    "handoff.flow3_ready": {State.READY_FOR_ADJUDICATION: SAME},
    # --- Flow 3a: two blinded reviews, compared by code ---
    "adjudication.assigned": {State.READY_FOR_ADJUDICATION: State.ADJ_REVIEW},
    "adjudication.reassigned": {State.ADJ_REVIEW: SAME, State.ADJ3_REVIEW: SAME},
    "adjudication.draft_saved": {State.ADJ_REVIEW: SAME, State.ADJ3_REVIEW: SAME},
    "adjudication.signed": {State.ADJ_REVIEW: SAME},
    "comparison.agreed": {State.ADJ_REVIEW: State.OUTCOME},
    "comparison.disputed": {State.ADJ_REVIEW: State.ADJ3_REVIEW},
    "comparison.to_committee": {State.ADJ_REVIEW: State.CEC_MEETING},  # no Adjudicator 3 left
    # --- Flow 3b: Adjudicator 3 on the disputed fields, then the committee ---
    "adj3.signed": {State.ADJ3_REVIEW: SAME},
    "adj3.resolved": {State.ADJ3_REVIEW: State.OUTCOME},
    "adj3.unresolved": {State.ADJ3_REVIEW: State.CEC_MEETING},
    "meeting.recorded": {State.CEC_MEETING: SAME},
    "consensus.signed": {State.CEC_MEETING: State.OUTCOME},
    "more_data.requested": {State.ADJ_REVIEW: State.MORE_DATA, State.CEC_MEETING: State.MORE_DATA},
    "committee.resumed": {State.READY_FOR_ADJUDICATION: State.CEC_MEETING},  # Q-06
    # --- Flow 4: outcome, export, close; re-adjudication opens a new round ---
    "outcome.derived": {State.OUTCOME: State.EXPORT},
    "export.sent": {State.EXPORT: SAME},
    "export.acknowledged": {State.EXPORT: State.CLOSED},
    "readjudication.started": {State.CLOSED: State.READY_FOR_ADJUDICATION},
    "case.withdrawn": dict.fromkeys(OPEN_STATES, State.WITHDRAWN),
    "source.changed": dict.fromkeys(ALL_STATES, SAME),
    "source.stale_ignored": dict.fromkeys(ALL_STATES, SAME),
    "case.flagged": dict.fromkeys(ALL_STATES, SAME),
    "notification.queued": dict.fromkeys(ALL_STATES, SAME),
    "notification.sent": dict.fromkeys(ALL_STATES, SAME),
    "notification.failed": dict.fromkeys(ALL_STATES, SAME),
    "ai.called": dict.fromkeys(ALL_STATES, SAME),
    "ai.gate": dict.fromkeys(ALL_STATES, SAME),
    "upload.rejected": dict.fromkeys(ALL_STATES, SAME),
}


class TransitionRefused(Exception):
    def __init__(self, event_type: str, state: State | None):
        self.event_type = event_type
        self.state = state
        super().__init__(f"event {event_type!r} is not allowed in state {state}")


def next_state(current: State | None, event_type: str) -> State:
    """Return the state after `event_type`, or raise TransitionRefused."""
    allowed = TRANSITIONS.get(event_type)
    if allowed is None or current not in allowed:
        raise TransitionRefused(event_type, current)
    target = allowed[current]
    if target == SAME:
        assert current is not None
        return current
    return State(target)


class CaseState(BaseModel):
    case_id: str
    state: State
    state_since: str
    seq: int
    last_hash: str
    created_at: str
    study: str | None = None
    subject: str | None = None
    ae_id: str | None = None
    flags: list[str] = []

    def to_json(self) -> str:
        return canonical_json(self.model_dump(mode="json")) + "\n"


def _add_flag(flags: list[str], flag: str) -> list[str]:
    return flags if flag in flags else [*flags, flag]


def replay(events: list[dict[str, Any]]) -> CaseState:
    """Rebuild a case's state from its full list of event records.
    Raises TransitionRefused on any illegal step."""
    if not events:
        raise ValueError("a case has at least one event")
    state: State | None = None
    cs: dict[str, Any] = {}
    for ev in events:
        new = next_state(state, ev["type"])
        data = ev.get("data") or {}
        if ev["type"] == "case.created":
            cs = {
                "case_id": ev["case"],
                "created_at": ev["at"],
                "study": data.get("study"),
                "subject": data.get("subject"),
                "ae_id": data.get("ae_id"),
                "flags": [],
                "state_since": ev["at"],
            }
        elif ev["type"] == "source.changed":
            cs["flags"] = _add_flag(cs["flags"], "source_changed")
        elif ev["type"] == "case.flagged" and data.get("flag"):
            cs["flags"] = _add_flag(cs["flags"], data["flag"])
        if new != state:
            cs["state_since"] = ev["at"]
        state = new
    assert state is not None
    return CaseState(**cs, state=state, seq=events[-1]["seq"], last_hash=events[-1]["hash"])


# A state in words, for people: the app and the Slack app (P-55) say the same.
STATE_WORDS = {
    State.EVENT_CREATED: "Case created",
    State.AWAITING_DOCUMENTS: "Waiting for documents",
    State.DEIDENTIFYING: "Removing identifiers",
    State.AWAITING_SITE_CONFIRMATION: "Waiting for site review",
    State.SUBMITTED: "Submitted",
    State.QC_CHECKS: "Quality check running",
    State.QC_REVIEW: "Waiting for QC review",
    State.QUERY_OPEN: "Query open with the site",
    State.NARRATIVE: "Narrative being drafted",
    State.NARRATIVE_REVIEW: "Waiting for narrative review",
    State.READY_FOR_ADJUDICATION: "Ready for adjudication",
    State.ADJ_REVIEW: "Independent reviews",
    State.ADJ3_REVIEW: "Adjudicator 3 review",
    State.CEC_MEETING: "Waiting for the committee",
    State.MORE_DATA: "More data requested",
    State.OUTCOME: "Outcome being derived",
    State.EXPORT: "Exporting to the EDC",
    State.CLOSED: "Closed",
    State.WITHDRAWN: "Withdrawn",
}
