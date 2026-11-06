"""Flow 1 case states, the transition table and replay (mvp-flow-1.md §4).

The current state of a case is never stored as truth: it is computed by replaying the
case's events through this table. case.json is only a cache of that result.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from adjud.events import Event, canonical_json


class State(StrEnum):
    EVENT_CREATED = "EVENT_CREATED"
    AWAITING_DOCUMENTS = "AWAITING_DOCUMENTS"
    DEIDENTIFYING = "DEIDENTIFYING"
    AWAITING_SITE_CONFIRMATION = "AWAITING_SITE_CONFIRMATION"
    SUBMITTED = "SUBMITTED"
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
AUTOMATIC_STATES = frozenset({State.EVENT_CREATED, State.DEIDENTIFYING})

SAME = "SAME"  # event is recorded but does not change state

# event type -> {allowed from-state: to-state or SAME}. `None` as from-state = new case.
TRANSITIONS: dict[str, dict[State | None, State | str]] = {
    "case.created": {None: State.EVENT_CREATED},
    "edc.related_data_pulled": {State.EVENT_CREATED: State.AWAITING_DOCUMENTS},
    "documents.uploaded": {
        State.AWAITING_DOCUMENTS: State.DEIDENTIFYING,
        State.AWAITING_SITE_CONFIRMATION: State.DEIDENTIFYING,
        State.DEIDENTIFYING: SAME,
    },
    "deid.file_processed": {State.DEIDENTIFYING: SAME},
    "deid.completed": {State.DEIDENTIFYING: State.AWAITING_SITE_CONFIRMATION},
    "deid.failed": {State.DEIDENTIFYING: State.AWAITING_DOCUMENTS},
    "redactions.changed": {State.AWAITING_SITE_CONFIRMATION: State.DEIDENTIFYING},
    "document.deleted": {
        State.AWAITING_DOCUMENTS: SAME,
        State.AWAITING_SITE_CONFIRMATION: SAME,
    },
    "submission.signed": {State.AWAITING_SITE_CONFIRMATION: State.SUBMITTED},
    "handoff.flow2_ready": {State.SUBMITTED: SAME},
    "case.withdrawn": dict.fromkeys(OPEN_STATES, State.WITHDRAWN),
    "source.changed": dict.fromkeys(ALL_STATES, SAME),
    "notification.queued": dict.fromkeys(ALL_STATES, SAME),
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


def replay(events: list[Event]) -> CaseState:
    """Rebuild a case's state from its full event list. Raises on any illegal step."""
    if not events:
        raise ValueError("a case has at least one event")
    state: State | None = None
    cs: dict[str, Any] = {}
    for ev in events:
        new = next_state(state, ev.type)
        if ev.type == "case.created":
            cs = {
                "case_id": ev.case,
                "created_at": ev.at,
                "study": ev.data.get("study"),
                "subject": ev.data.get("subject"),
                "ae_id": ev.data.get("ae_id"),
                "flags": [],
                "state_since": ev.at,
            }
        if ev.type == "source.changed" and "source_changed" not in cs["flags"]:
            cs["flags"] = [*cs["flags"], "source_changed"]
        if new != state:
            cs["state_since"] = ev.at
        state = new
    assert state is not None
    return CaseState(**cs, state=state, seq=events[-1].seq, last_hash=events[-1].hash)
