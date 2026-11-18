"""The Slack Agent's conversation log (plan P-58, Q18).

Every turn of a Conversation with the Agent, and every other text the Agent posts (the
greeting), is one record in the hash-chained `data/slack/conversations.jsonl`: the Slack
user, whom the Agent acted as, the case (if any), the tools called, and the reply as
posted or that it was held back by the PHI gate. What the person typed is never stored
here (as in the Slack actions log): people may type identifiers. A held reply is
recorded by the categories the gate found, never its text. Actions the Agent takes are
ordinary case events as that person; `done` says what they were, in counts and case IDs
(for an upload from Slack: how many files, and whether Slack kept the originals).
`attached` lists the Slack file ids of the message, so a file attached earlier in a
thread is uploaded only as the person the Agent acted as when it was attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adjud.chain import ChainedLog
from adjud.events import Clock, utc_now
from adjud.paths import DataPaths

LOG_NAME = "conversations.jsonl"


@dataclass
class ConversationLog:
    paths: DataPaths
    clock: Clock = field(default=utc_now)

    @property
    def log(self) -> ChainedLog:
        return ChainedLog(self.paths.root / "slack" / LOG_NAME, clock=self.clock)

    def record(
        self,
        *,
        slack_user: str,
        acting_as: str | None,
        channel: str,
        thread: str,
        kind: str,  # turn | greeting
        outcome: str,  # posted | held | refused | failed
        case_id: str | None = None,
        source: str = "code",  # ai | code (the Agent's AI did not answer)
        mode: str | None = None,
        simulated: bool = False,
        tools: list[dict[str, Any]] | None = None,
        reply: str | None = None,
        held: dict[str, Any] | None = None,
        ai_call: dict[str, Any] | None = None,
        note: str | None = None,
        attachments: int = 0,
        done: list[dict[str, Any]] | None = None,
        attached: list[str] | None = None,  # Slack file ids attached to the message
    ) -> None:
        entry: dict[str, Any] = {
            "slack_user": slack_user,
            "acting_as": acting_as,
            "channel": channel,
            "thread": thread,
            "kind": kind,
            "case_id": case_id,
            "source": source,
            "mode": mode,
            "simulated": simulated,
            "tools": [{"tool": t["tool"], "error": bool(t.get("error"))} for t in tools or []],
            "outcome": outcome,
        }
        if reply is not None:  # posted, so it passed the PHI gate
            entry["reply"] = reply
        if held is not None:
            entry["held"] = held
        if ai_call is not None:
            entry["ai_call"] = ai_call
        if note:
            entry["note"] = note
        if attachments:  # files attached to the message, counted (names may identify)
            entry["attachments"] = attachments
        if attached:  # ids only: whom the Agent acted as when each file was attached
            entry["attached"] = attached
        if done:
            entry["done"] = done
        self.log.append(entry)

    def attached_by(self, slack_user: str, person: str) -> set[str]:
        """The Slack files this Slack user attached while the Agent acted as `person`:
        only those may be uploaded later in a thread as that person (Q5)."""
        return {fid for e in self.log.records()
                if e.get("slack_user") == slack_user and e.get("acting_as") == person
                for fid in e.get("attached") or []}  # fmt: skip
