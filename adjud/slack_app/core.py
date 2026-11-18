"""What the Slack app knows, with no Slack library and no Clinical Content (ADR 0004).

Everything here is built from case IDs, workflow states, site names, people's names and
counts: never from the AE snapshot, documents, queries, narratives or answers. A Task
says what is waiting in words and where it is finished: in the app (signed steps and
anything that needs Clinical Content) or, for the data manager's import, in Slack.

Acting As (demo): one Slack account takes the part of one demo person, as the app's
sidebar does; simulated, not enforced. Real use maps each Slack user to one person with
`slack_user_id` in users.yaml.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml

from adjud.chain import ChainedLog
from adjud.edc_pull import pull
from adjud.events import Clock, utc_now
from adjud.flow3 import Flow3, assigned_cases, current_round
from adjud.intake import ImportResult, IntakePipeline
from adjud.paths import DataPaths
from adjud.store import FileCaseStore, write_atomic
from adjud.workflow import State, state_words

ROLE_WORDS = {
    "data_manager": "Data manager",
    "site": "Site user",
    "safety_specialist": "Safety Specialist",
    "cec_coordinator": "CEC Coordinator",
    "adjudicator": "Adjudicator",
}
ROLE_ORDER = list(ROLE_WORDS)
FLAG_WORDS = {
    "source_changed": "changed in the EDC",
    "no_longer_eligible": "no longer meets the rules",
    "possible_duplicate": "a possible duplicate",
}
# The Suggested Outcome (computed by code in flow2.py) as the one word Slack may show
# (ADR 0004): never its reasons or any AI text.
NOTHING_NEXT = "Nothing more for you on this case now."
SUGGESTED_WORDS = {"pass": "pass", "query": "query", "unclear": "none"}


def suggested_word(proposal: str | None) -> str:
    return SUGGESTED_WORDS.get(proposal or "", "none")


@dataclass(frozen=True)
class Task:
    """One step waiting for one person on one case (GLOSSARY: Task)."""

    case_id: str | None
    words: str  # e.g. "Review the redacted pages and sign": never Clinical Content
    page: str  # the app page where it is finished ("import" for the data manager's queue)
    in_slack: str | None = None  # the Slack action that does it here, e.g. "import_queued"


@dataclass(frozen=True)
class Status:
    case_id: str
    state: str  # in words
    since: str  # YYYY-MM-DD
    next_step: str
    site: str


@dataclass
class Directory:
    """The demo people (users.yaml), the study's site names, and Acting As."""

    config_dir: Path
    slack_dir: Path
    study: str = "DEMO01"

    def __post_init__(self) -> None:
        self.users: list[dict[str, Any]] = yaml.safe_load(
            (self.config_dir / "users.yaml").read_text()
        )["users"]
        cfg = yaml.safe_load((self.config_dir / self.study / "study.yaml").read_text()) or {}
        self.site_names: dict[str, str] = {str(k): v for k, v in cfg.get("site_names", {}).items()}

    @property
    def by_id(self) -> dict[str, dict[str, Any]]:
        return {u["id"]: u for u in self.users}

    def site_name(self, site: str | None) -> str:
        return self.site_names.get(str(site), f"site {site}")

    def describe(self, person: dict[str, Any]) -> str:
        """'Priya Nair (Site user, Larkfield General Hospital)'."""
        role = ROLE_WORDS.get(person["role"], person["role"])
        where = f", {self.site_name(person['site'])}" if person["role"] == "site" else ""
        chair = ", committee Chair" if person.get("chair") else ""
        return f"{person['name']} ({role}{where}{chair})"

    # -- Acting As ---------------------------------------------------------------------------

    @property
    def acting_path(self) -> Path:
        return self.slack_dir / "acting_as.json"

    def acting(self) -> dict[str, str]:
        """Slack user id -> demo person id."""
        if not self.acting_path.exists():
            return {}
        return json.loads(self.acting_path.read_text())

    def act_as(self, slack_user: str, person_id: str) -> dict[str, Any]:
        if person_id not in self.by_id:
            raise KeyError(person_id)
        # Re-inserted at the end, so the last account to choose is the demo presenter.
        mapping = {k: v for k, v in self.acting().items() if k != slack_user}
        mapping[slack_user] = person_id
        write_atomic(self.acting_path, json.dumps(mapping, indent=1).encode(), overwrite=True)
        return self.by_id[person_id]

    def person_for(self, slack_user: str) -> dict[str, Any] | None:
        """Whom this Slack user is: their Acting As choice, else the person whose
        `slack_user_id` it is (real use), else no one yet."""
        chosen = self.acting().get(slack_user)
        if chosen in self.by_id:
            return self.by_id[chosen]
        return next((u for u in self.users if u.get("slack_user_id") == slack_user), None)

    def slack_user_for(self, person_id: str) -> str | None:
        """Where a notice for this person goes: their own Slack user (real use), else the
        demo presenter, the Slack account that last chose someone to act as."""
        own = self.by_id.get(person_id, {}).get("slack_user_id")
        if own:
            return own
        acting = self.acting()
        return next(reversed(acting), None) if acting else None

    def find(self, words: str) -> list[dict[str, Any]]:
        """People whose id or name contains every word, e.g. 'anna' or 'site 102'."""
        parts = [w for w in re.split(r"\s+", words.lower().strip()) if w]
        out = []
        for u in self.users:
            hay = f"{u['id']} {u['name']} {ROLE_WORDS.get(u['role'], '')} {u.get('site', '')}"
            if parts and all(p in hay.lower() for p in parts):
                out.append(u)
        return out


@dataclass
class SlackActionsLog:
    """Every Slack interaction, hash-chained (ADR 0003): who in Slack, acting as whom,
    what, on which cases, and the result. Case events stay as they would be from the app."""

    paths: DataPaths
    clock: Clock = field(default=utc_now)

    @property
    def log(self) -> ChainedLog:
        return ChainedLog(self.paths.root / "slack" / "actions.jsonl", clock=self.clock)

    def record(
        self,
        slack_user: str,
        acting_as: str | None,
        action: str,
        *,
        cases: list[str] | None = None,
        result: str = "done",
        reason: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "slack_user": slack_user,
            "acting_as": acting_as,
            "action": action,
            "cases": cases or [],
            "result": result,
        }
        if reason:
            entry["reason"] = reason
        self.log.append(entry)


@dataclass
class Workspace:
    """Tasks, cases and status for one person, read from the case records."""

    paths: DataPaths
    study: str = "DEMO01"
    base_url: str = "http://localhost:8501"
    clock: Clock = field(default=utc_now)

    def __post_init__(self) -> None:
        self.pipe = IntakePipeline(
            self.paths.root, self.paths.config, self.paths.edc_mock, self.study, self.clock
        )
        self.store: FileCaseStore = self.pipe.store
        self.people = Directory(self.paths.config, self.paths.root / "slack", self.study)
        self.flow3 = Flow3(self.store, self.paths.config, self.study)

    # -- who sees what (as the app's Context.sees) ------------------------------------------

    def cases(self, person: dict[str, Any]) -> list[Any]:
        states = self.store.list_case_states()
        if person["role"] == "adjudicator":
            mine = assigned_cases(self.store, person["id"], bool(person.get("chair")))
            return [cs for cs in states if cs.case_id in mine]
        if person["role"] == "site":
            return [cs for cs in states if _site_of(cs.subject) == str(person.get("site"))]
        return states

    def find_case(self, person: dict[str, Any], text: str) -> str | None:
        """'DEMO01-0001' or '0001', among this person's cases only."""
        ids = [cs.case_id for cs in self.cases(person)]
        t = text.strip().upper()
        if t in ids:
            return t
        if m := re.fullmatch(r"\d{1,4}", t):
            return next((c for c in ids if c.endswith(f"-{m.group(0).zfill(4)}")), None)
        return None

    # -- links into the app ------------------------------------------------------------------

    def link(self, person: dict[str, Any], page: str, case_id: str | None = None) -> str:
        """The app page for this person, as e-mail links open it (ui/app.py reads role,
        user and case from the query string)."""
        query = {"role": person["role"], "user": person["id"]}
        if case_id:
            query["case"] = case_id
        return f"{self.base_url}/{page}?{urlencode(query)}"

    def waiting_files(self) -> list[Path]:
        """AE files in the import queue, as process_inbox sees them (no hidden files)."""
        return self.paths.waiting_files()

    # -- Tasks --------------------------------------------------------------------------------

    def tasks(self, person: dict[str, Any]) -> list[Task]:
        out: list[Task] = []
        role = person["role"]
        if role == "data_manager":
            waiting = self.waiting_files()
            if waiting:
                n = len(waiting)
                out.append(Task(None, f"{n} AE file{'s' if n > 1 else ''} waiting to be "
                                "imported", "import", "import_queued"))  # fmt: skip
        for cs in self.cases(person):
            task = self._task(person, cs)
            if task:
                out.append(task)
        return out

    def _task(self, person: dict[str, Any], cs: Any) -> Task | None:
        role, cid, state = person["role"], cs.case_id, cs.state
        if role == "data_manager" and cs.flags:
            words = " and ".join(FLAG_WORDS.get(f, f.replace("_", " ")) for f in cs.flags)
            return Task(cid, f"Check the case: {words}", "import")
        if role == "site":
            site_steps = {
                State.AWAITING_DOCUMENTS: "Upload the documents",
                State.AWAITING_SITE_CONFIRMATION: "Review the redacted pages and sign",
                State.QUERY_OPEN: "Answer the query (read it in the app)",
            }
            return Task(cid, site_steps[state], "documents") if state in site_steps else None
        if role == "safety_specialist":
            if state == State.QC_REVIEW:
                word = self.suggested(cid)
                return Task(cid, f"Review the Quality Check and sign · suggested: {word}", "qc")
            if state == State.NARRATIVE_REVIEW:
                return Task(cid, "Check and sign the narrative", "qc")
            return None
        if role == "cec_coordinator" and state == State.CEC_MEETING:
            return Task(cid, "Record the committee meeting", "committee")
        if role == "adjudicator":
            rnd = current_round(self.store, cid)
            slot = self.flow3.slot_of(cid, person["id"])
            if rnd and slot and slot not in rnd.signed:
                if slot == "3":
                    return Task(cid, "Review the disputed questions as Adjudicator 3", "adjudicate")
                return Task(cid, f"Your independent review (round {rnd.number})", "adjudicate")
            meeting = rnd.meeting if rnd else None
            if (state == State.CEC_MEETING and person.get("chair") and meeting is not None
                    and meeting["data"]["quorum_met"]):  # fmt: skip
                return Task(cid, "Sign the committee's consensus", "committee")
        return None

    def next_step(self, person: dict[str, Any], case_id: str) -> str:
        """What this person does next on this case, worded as their Task on App Home, or
        that nothing more is waiting for them there (P-57: a DM guides, not just notifies)."""
        for task in self.tasks(person):
            if task.case_id == case_id:
                return task.words
        return NOTHING_NEXT

    def suggested(self, case_id: str) -> str:
        """The latest Quality Check round's Suggested Outcome as one word, read from the
        proposal on its `qc.checks_completed` event only (no results file, no reasons)."""
        proposal = None
        for e in self.store.events(case_id):
            if e["type"] == "qc.checks_completed":
                proposal = e["data"].get("proposal")
        return suggested_word(proposal)

    def records_not_a_case(self, person: dict[str, Any]) -> int:
        """For the data manager: how many AE records are, at their latest evaluation, not
        eligible and not a case. A count only; the records and reasons stay in the app."""
        if person["role"] != "data_manager":
            return 0
        latest: dict[str, dict[str, Any]] = {}
        for e in self.store.intake_log().records():
            latest[e["key"]] = e
        return sum(1 for e in latest.values()
                   if not e.get("case_id") and e.get("eligible") is False)  # fmt: skip

    # -- status --------------------------------------------------------------------------------

    def status(self, person: dict[str, Any], case_id: str) -> Status:
        cs = self.store.state(case_id)
        task = self._task(person, cs)
        next_step = task.words if task else "Nothing is waiting for you on this case."
        return Status(
            case_id=case_id,
            state=state_words(cs.state, person["role"]),
            since=cs.state_since[:10],
            next_step=next_step,
            site=self.people.site_name(_site_of(cs.subject)),
        )

    # -- the data manager's import (the same call as the app's button) -----------------------

    def import_queued(self, person: dict[str, Any]) -> tuple[str, list[str]]:
        """Import the queued AE files as this person. Returns a summary in counts and case
        IDs only, and the cases created: no subject or AE IDs (ADR 0004)."""
        if person["role"] != "data_manager":
            raise PermissionError("Importing AE records is the data manager's step.")
        if not self.waiting_files():
            return "There is no AE file waiting in the import queue.", []
        results = self.pipe.process_inbox(
            self.paths.inbox, self.paths.processed, actor=person["id"]
        )
        return summarise_import(results)

    def pull_from_edc(self, person: dict[str, Any]) -> tuple[str, list[str]]:
        """The app's 'Pull AE records now' as this person (adjud/edc_pull.pull): the mock
        EDC's records and any queued file are imported. Counts and case IDs only."""
        if person["role"] != "data_manager":
            raise PermissionError("Pulling AE records from the EDC is the data manager's step.")
        summary, created = summarise_import(pull(self.pipe, person["id"]))
        return "Pulled the AE records from the EDC. " + summary, created


def summarise_import(results: list[ImportResult]) -> tuple[str, list[str]]:
    """An import in counts and case IDs only, and the cases created: no subject or AE IDs
    (ADR 0004)."""
    outcomes = [o for r in results for o in r.outcomes]
    created = [o["case_id"] for o in outcomes if o.get("outcome") == "created"]
    others = len(outcomes) - len(created)
    rejected = sum(1 for r in results if r.status != "ACCEPTED")
    parts = [f"Imported {len(results)} file{'s' if len(results) != 1 else ''}: "
             f"{len(outcomes)} record{'s' if len(outcomes) != 1 else ''}"]  # fmt: skip
    parts.append(f"{len(created)} new case{'s' if len(created) != 1 else ''}"
                 + (f" ({', '.join(created)})" if created else ""))  # fmt: skip
    if others:
        parts.append(f"{others} not a new case (the reasons are on AE import in the app)")
    if rejected:
        parts.append(f"{rejected} file{'s' if rejected > 1 else ''} rejected")
    return "; ".join(parts) + ".", created


def _site_of(subject: str | None) -> str | None:
    parts = (subject or "").split("-")
    return parts[1] if len(parts) > 1 else None
