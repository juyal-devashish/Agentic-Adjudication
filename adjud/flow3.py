"""Flow 3: adjudication (spec §6.5, §6.6; plan P-41 to P-49).

People adjudicate; code assigns, compares and routes. No AI takes part (P-49).

Automatic steps, run by the runner:
    start_round     READY_FOR_ADJUDICATION → ADJ_REVIEW: Adjudicators 1 and 2 assigned by
                    round-robin, never from the case's site; the same people again after
                    more data or for a re-adjudication. After more data the committee asked
                    for, the case returns to the committee instead (Q-06).
    compare         both reviews signed → every compared field agrees: OUTCOME; otherwise
                    Adjudicator 3 gets the disputed fields only (ADJ3_REVIEW, blinded)
    resolve_adj3    Adjudicator 3 matches 1 or 2 on every disputed field: OUTCOME;
                    otherwise the committee gets what is left (CEC_MEETING)
    send_more_data  MORE_DATA → a query to the site (QUERY_OPEN); its answer goes through
                    Flow 1 and the Quality Check again (D-06)

Human actions:
    sign_review         an assigned adjudicator signs their answers (Adjudicator 1, 2 or 3)
    save_draft          an unsigned draft, seen only by its author (ASN-07)
    reassign            the CEC Coordinator moves an open review to someone else, with a reason
    request_more_data   an adjudicator (in review) or the Coordinator (for the committee),
                        signed: no query reaches the site without a person's signature
    record_meeting      the Coordinator records the committee meeting
    sign_consensus      the Chair signs the committee's decisions

A *round* is one adjudication from assignment to outcome; re-adjudication starts the
next one. A *pass* is one set of reviews within a round; more data starts the next pass,
and the earlier signed reviews stay in the record, superseded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from adjud.events import EventDraft, sha256_bytes
from adjud.forms import Form, FormRefused
from adjud.notify import Outbox
from adjud.signatures import SignatureRefused, load_users, sign
from adjud.store import FileCaseStore, json_bytes
from adjud.workflow import State, replay

SYSTEM_CEC = "system:cec"
REVIEW_MEANING = "Adjudication assessment"
ADJ3_MEANING = "Adjudication assessment of the disputed fields"
CONSENSUS_MEANING = "Committee consensus"
MEETING_CHANGED = "the meeting record changed since you opened it; review the new record"
MORE_DATA_MEANING = "More data requested for adjudication — a query is sent to the site"
SLOTS = ("1", "2")


class Flow3Refused(Exception):
    pass


def _site_of(store: FileCaseStore, case_id: str) -> str:
    subject = store.state(case_id).subject or "--"
    return subject.split("-")[1]


@dataclass
class Round:
    """Where a case's adjudication stands, read from its events."""

    number: int
    passes: int
    form: dict[str, Any]  # {path, sha256, id, version}
    manifest: dict[str, Any]
    holders: dict[str, str]  # slot ("1", "2", "3") -> user id, after reassignments
    signed: dict[str, dict[str, Any]]  # slot -> its signing event, this pass
    disputed: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    agreed: dict[str, Any] = field(default_factory=dict)  # settled values this pass
    meeting: dict[str, Any] | None = None  # the latest meeting.recorded event


def current_round(store: FileCaseStore, case_id: str) -> Round | None:
    """The latest round and pass of a case, or None before the first assignment."""
    return round_of(store.events(case_id))


def round_of(events: list[dict[str, Any]]) -> Round | None:
    """current_round from a list of events, such as the prior events under the lock."""
    starts = [i for i, e in enumerate(events) if e["type"] == "adjudication.assigned"]
    if not starts:
        return None
    first = events[starts[-1]]["data"]
    number = first["round"]
    passes = sum(events[i]["data"]["round"] == number for i in starts)
    rnd = Round(number=number, passes=passes, form=first["form"], manifest=first["manifest"],
                holders=dict(first["adjudicators"]), signed={})  # fmt: skip
    for e in events[starts[-1] + 1 :]:
        d = e["data"]
        if e["type"] == "adjudication.reassigned":
            rnd.holders[d["slot"]] = d["to"]
        elif e["type"] in ("adjudication.signed", "adj3.signed"):
            rnd.signed[d["slot"]] = e
        elif e["type"] == "comparison.disputed":
            rnd.disputed, rnd.agreed = d["disputed"], d["agreed"]
            if d.get("adjudicator3"):
                rnd.holders["3"] = d["adjudicator3"]
        elif e["type"] == "comparison.to_committee":
            rnd.disputed = rnd.unresolved = d["disputed"]
            rnd.agreed = d["agreed"]
        elif e["type"] == "adj3.unresolved":
            rnd.unresolved = d["unresolved"]
            rnd.agreed = d["agreed"]
        elif e["type"] == "meeting.recorded":
            rnd.meeting = e
    return rnd


def _slot(rnd: Round | None, state: State, user_id: str) -> str | None:
    if rnd is None:
        return None
    if state == State.ADJ_REVIEW:
        return next((s for s in SLOTS if rnd.holders.get(s) == user_id), None)
    if state == State.ADJ3_REVIEW and rnd.holders.get("3") == user_id:
        return "3"
    return None


def _open_slots(state: State) -> tuple[str, ...]:
    return SLOTS if state == State.ADJ_REVIEW else ("3",) if state == State.ADJ3_REVIEW else ()


COMMITTEE_EVENTS = ("adj3.unresolved", "comparison.to_committee", "committee.resumed")


def first_visible_seq(
    store: FileCaseStore, case_id: str, user_id: str, chair: bool = False
) -> int | None:
    """The seq of the case's first event that gives this adjudicator the case (see
    `assigned_cases`), or None while they have none."""
    for e in store.events(case_id):
        d = e["data"]
        if (
            (chair and e["type"] in COMMITTEE_EVENTS)
            or (e["type"] == "adjudication.assigned" and user_id in d["adjudicators"].values())
            or (e["type"] == "adjudication.reassigned" and d["to"] == user_id)
            or (e["type"] == "comparison.disputed" and d.get("adjudicator3") == user_id)
        ):
            return e["seq"]
    return None


def assigned_cases(store: FileCaseStore, user_id: str, chair: bool = False) -> set[str]:
    """Cases this adjudicator holds or held a review on, in any round (ACC-02); for the
    committee's Chair, also every case that went to the committee."""
    return {c for c in store.list_cases()
            if first_visible_seq(store, c, user_id, chair) is not None}  # fmt: skip


@dataclass
class Flow3:
    store: FileCaseStore
    config_dir: Path
    study: str = "DEMO01"
    outbox: Outbox = field(init=False)
    cfg: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.outbox = Outbox(self.store.paths, self.study, clock=self.store.clock)
        study = yaml.safe_load((self.config_dir / self.study / "study.yaml").read_text())
        self.cfg = study.get("adjudication") or {}

    # -- helpers -------------------------------------------------------------------------

    def form(self, case_id: str | None = None) -> Form:
        """The round's own copy of the form (fixed for the round, ADJ-01), or the study's
        current form before the first assignment."""
        rnd = current_round(self.store, case_id) if case_id else None
        if rnd is not None:
            return Form(**self.store.get_json(case_id, rnd.form["path"]))
        return Form.load(self.config_dir, self.study, self.cfg.get("form", "mi_v1"))

    def users(self) -> dict[str, dict[str, Any]]:
        return load_users(self.config_dir)

    def pool(self, case_id: str) -> list[str]:
        """Adjudicators who may review this case: never one affiliated with its site
        (ASN-03, ACC-06), in users.yaml order."""
        site = _site_of(self.store, case_id)
        return [u["id"] for u in self.users().values() if u["role"] == "adjudicator"
                and site not in [str(s) for s in u.get("affiliated_sites") or []]]  # fmt: skip

    def chair(self) -> str | None:
        return next((u["id"] for u in self.users().values()
                     if u["role"] == "adjudicator" and u.get("chair")), None)  # fmt: skip

    def _event(self, case_id: str, type_: str, actor: str, data: dict[str, Any],
               expect: Callable[[list[dict[str, Any]]], None] | None = None,
               **kw: Any) -> None:  # fmt: skip
        """Append one event; `expect(prior events)` re-checks under the case lock."""
        self.store.append_event(case_id, EventDraft(type=type_, actor=actor, data=data, **kw),
                                expect=expect)  # fmt: skip

    def _notify(self, case_id: str, to: str, template: str, actor: str) -> None:
        role = None
        if to.startswith("user:"):
            role = self.users()[to.removeprefix("user:")]["role"]
        page = "committee" if template == "consensus_to_sign" else None  # the Chair signs there
        note = self.outbox.queue(case_id, to, template, role, page)
        self._event(case_id, "notification.queued", actor,
                    {"to": to, "template": template, "id": note["id"]})  # fmt: skip

    def _user(self, user_id: str, role: str) -> dict[str, Any]:
        user = self.users().get(user_id)
        if user is None or user["role"] != role:
            raise SignatureRefused(f"only a {role.replace('_', ' ')} can do this")
        return user

    def _state(self, case_id: str) -> State:
        return self.store.state(case_id).state

    # -- automatic steps -----------------------------------------------------------------

    def pending_starts(self) -> list[str]:
        """READY_FOR_ADJUDICATION cases whose Flow 3 package is current: written after the
        latest Quality Check signature (a re-adjudication reuses it)."""
        out = []
        for cs in self.store.list_case_states(State.READY_FOR_ADJUDICATION):
            types = [e["type"] for e in self.store.events(cs.case_id)]
            approvals = [i for i, t in enumerate(types)
                         if t in ("quality_check.signed", "narrative.signed",
                                  "narrative.approved")]  # fmt: skip
            if approvals and "handoff.flow3_ready" in types[approvals[-1] :]:
                out.append(cs.case_id)
        return out

    def _handoff(self, case_id: str) -> dict[str, str]:
        e = next(e for e in reversed(self.store.events(case_id))
                 if e["type"] == "handoff.flow3_ready")  # fmt: skip
        return e["data"]["manifest"]

    def _round_robin(self, case_id: str) -> list[str]:
        """The pool rotated by the number of first assignments made so far, study-wide
        (ASN-01): deterministic, and the work is spread evenly."""
        pool = self.pool(case_id)
        if len(pool) < 2:
            raise Flow3Refused(f"{case_id}: fewer than two adjudicators may review it")
        made = sum(
            1
            for cid in self.store.list_cases()
            for e in self.store.events(cid)
            if e["type"] == "adjudication.assigned" and e["data"]["round"] == 1
            and e["data"]["pass"] == 1
        )  # fmt: skip
        k = made % len(pool)
        return pool[k:] + pool[:k]

    def start_round(self, case_id: str) -> str:
        """Assign the reviews, or return the case to the committee (Q-06). Returns the
        event type written."""
        events = self.store.events(case_id)
        rnd = current_round(self.store, case_id)
        restarted = [e for e in events if e["type"] == "readjudication.started"]
        number = len(restarted) + 1
        last_start = max((i for i, e in enumerate(events)
                          if e["type"] in ("adjudication.assigned", "committee.resumed")),
                         default=-1)  # fmt: skip
        asked = [e for e in events[last_start + 1 :] if e["type"] == "more_data.requested"]
        manifest = self._handoff(case_id)
        same_round = rnd is not None and rnd.number == number
        if same_round and asked and asked[-1]["data"]["from"] == "committee":
            self._event(case_id, "committee.resumed", SYSTEM_CEC,
                        {"round": number, "manifest": manifest})  # fmt: skip
            self._notify(case_id, "role:cec_coordinator", "committee_needed", SYSTEM_CEC)
            return "committee.resumed"
        if rnd is not None:  # more data (same round) or re-adjudication (next round): same people
            holders = {s: rnd.holders[s] for s in SLOTS}
        else:
            order = self._round_robin(case_id)
            holders = {"1": order[0], "2": order[1]}
        if rnd is not None and rnd.number == number:
            form_ref, passes = rnd.form, rnd.passes + 1
        else:
            form = Form.load(self.config_dir, self.study, self.cfg.get("form", "mi_v1"))
            rel = f"adjudication/round{number}/form_{form.form_id}_v{form.version}.json"
            sha = self.store.put_json_once(case_id, rel, form.model_dump(mode="json"))
            form_ref = {"path": rel, "sha256": sha, "id": form.form_id, "version": form.version}
            passes = 1
        self._event(case_id, "adjudication.assigned", SYSTEM_CEC,
                    {"round": number, "pass": passes, "form": form_ref, "manifest": manifest,
                     "adjudicators": holders})  # fmt: skip
        for slot in SLOTS:
            self._notify(case_id, f"user:{holders[slot]}", "adjudication_assigned", SYSTEM_CEC)
        return "adjudication.assigned"

    def pending_comparisons(self) -> list[str]:
        out = []
        for cs in self.store.list_case_states(State.ADJ_REVIEW):
            rnd = current_round(self.store, cs.case_id)
            if rnd and all(s in rnd.signed for s in SLOTS):
                out.append(cs.case_id)
        for cs in self.store.list_case_states(State.ADJ3_REVIEW):
            rnd = current_round(self.store, cs.case_id)
            if rnd and "3" in rnd.signed:
                out.append(cs.case_id)
        return out

    def _answers(self, case_id: str, signed: dict[str, Any]) -> dict[str, Any]:
        return self.store.get_json(case_id, signed["data"]["answers"]["path"])

    def compare(self, case_id: str) -> str:
        """Code compares, field by field (ADJ-06 to ADJ-09). Returns the event type."""
        if self._state(case_id) == State.ADJ3_REVIEW:
            return self.resolve_adj3(case_id)
        rnd = current_round(self.store, case_id)
        form = self.form(case_id)
        a1, a2 = (self._answers(case_id, rnd.signed[s]) for s in SLOTS)
        rows = [{"field": f, "1": a1.get(f), "2": a2.get(f),
                 "agree": form.field(f).agree(a1.get(f), a2.get(f))}
                for f in form.compared_ids]  # fmt: skip
        base = f"adjudication/round{rnd.number}/pass{rnd.passes}"
        sha = self.store.put_json_once(case_id, f"{base}/comparison.json", rows)
        ref = {"path": f"{base}/comparison.json", "sha256": sha}
        disputed = form.with_dependents([r["field"] for r in rows if not r["agree"]])
        agreed = {r["field"]: r["1"] for r in rows if r["field"] not in disputed}
        if not disputed:
            self._event(case_id, "comparison.agreed", SYSTEM_CEC, {"comparison": ref})
            return "comparison.agreed"
        third = next((u for u in self._round_robin(case_id)
                      if u not in (rnd.holders["1"], rnd.holders["2"])), None)  # fmt: skip
        data = {"comparison": ref, "disputed": disputed, "agreed": agreed, "adjudicator3": third}
        if third is None:  # nobody left to be Adjudicator 3: the committee decides
            self._event(case_id, "comparison.to_committee", SYSTEM_CEC, data)
            self._notify(case_id, "role:cec_coordinator", "committee_needed", SYSTEM_CEC)
            return "comparison.to_committee"
        self._event(case_id, "comparison.disputed", SYSTEM_CEC, data)
        self._notify(case_id, f"user:{third}", "adj3_assigned", SYSTEM_CEC)
        return "comparison.disputed"

    def resolve_adj3(self, case_id: str) -> str:
        rnd = current_round(self.store, case_id)
        form = self.form(case_id)
        a1, a2 = (self._answers(case_id, rnd.signed[s]) for s in SLOTS)
        a3 = self._answers(case_id, rnd.signed["3"])
        rows = []
        for f in rnd.disputed:
            fld = form.field(f)
            match = "1" if fld.agree(a3.get(f), a1.get(f)) else (
                "2" if fld.agree(a3.get(f), a2.get(f)) else None)  # fmt: skip
            rows.append({"field": f, "3": a3.get(f), "matches": match})
        base = f"adjudication/round{rnd.number}/pass{rnd.passes}"
        sha = self.store.put_json_once(case_id, f"{base}/adjudicator3_result.json", rows)
        ref = {"path": f"{base}/adjudicator3_result.json", "sha256": sha}
        unresolved = [f for f in form.with_dependents([r["field"] for r in rows
                                                       if not r["matches"]])
                      if f in rnd.disputed]  # fmt: skip
        agreed = {**rnd.agreed,
                  **{r["field"]: r["3"] for r in rows if r["field"] not in unresolved}}  # fmt: skip
        if not unresolved:
            self._event(case_id, "adj3.resolved", SYSTEM_CEC, {"result": ref, "agreed": agreed})
            return "adj3.resolved"
        self._event(case_id, "adj3.unresolved", SYSTEM_CEC,
                    {"result": ref, "agreed": agreed, "unresolved": unresolved})  # fmt: skip
        self._notify(case_id, "role:cec_coordinator", "committee_needed", SYSTEM_CEC)
        return "adj3.unresolved"

    def send_more_data(self, case_id: str) -> None:
        """MORE_DATA → a query to the site, carrying the signed request it comes from but
        not who signed it (ACC-04)."""
        asked = next(e for e in reversed(self.store.events(case_id))
                     if e["type"] == "more_data.requested")  # fmt: skip
        self._event(case_id, "query.opened", SYSTEM_CEC,
                    {"codes": ["more_data"], "text": asked["data"]["text"],
                     "request": asked["data"]["request"]})  # fmt: skip
        subject = self.store.state(case_id).subject or "--"
        self._notify(case_id, f"site:{subject.split('-')[1]}", "query_opened", SYSTEM_CEC)

    # -- adjudicators ----------------------------------------------------------------------

    def slot_of(self, case_id: str, user_id: str) -> str | None:
        """The review this person holds in the current pass, if it is open."""
        return _slot(current_round(self.store, case_id), self._state(case_id), user_id)

    def fields_for(self, case_id: str, slot: str) -> list[str]:
        """Adjudicators 1 and 2 answer the whole form; Adjudicator 3 the disputed fields."""
        if slot == "3":
            return current_round(self.store, case_id).disputed
        return [f.id for f in self.form(case_id).fields]

    def save_draft(self, case_id: str, user_id: str, answers: dict[str, Any]) -> None:
        slot = self.slot_of(case_id, user_id)
        if slot is None or slot in current_round(self.store, case_id).signed:
            raise Flow3Refused("you have no open review on this case")
        rnd = current_round(self.store, case_id)
        base = f"adjudication/round{rnd.number}/pass{rnd.passes}/drafts"
        n = sum(1 for e in self.store.events(case_id) if e["type"] == "adjudication.draft_saved"
                and e["actor"] == user_id) + 1  # fmt: skip
        rel = f"{base}/{user_id}_v{n}.json"
        sha = self.store.put_json(case_id, rel, answers)
        self._event(case_id, "adjudication.draft_saved", user_id,
                    {"round": rnd.number, "pass": rnd.passes, "slot": slot,
                     "draft": {"path": rel, "sha256": sha}})  # fmt: skip

    def draft(self, case_id: str, user_id: str) -> dict[str, Any]:
        """This person's latest draft in the current pass (never anyone else's, ASN-07)."""
        rnd = current_round(self.store, case_id)
        if rnd is None:
            return {}
        e = next((e for e in reversed(self.store.events(case_id))
                  if e["type"] == "adjudication.draft_saved" and e["actor"] == user_id
                  and e["data"]["round"] == rnd.number and e["data"]["pass"] == rnd.passes),
                 None)  # fmt: skip
        return self.store.get_json(case_id, e["data"]["draft"]["path"]) if e else {}

    def sign_review(self, case_id: str, user_id: str, password: str,
                    answers: dict[str, Any]) -> None:  # fmt: skip
        """An assigned adjudicator signs their answers. Signed answers never change."""
        slot = self.slot_of(case_id, user_id)
        rnd = current_round(self.store, case_id)
        if slot is None:
            raise Flow3Refused("you have no open review on this case")
        if slot in rnd.signed:
            raise Flow3Refused("you have already signed this review")
        form = self.form(case_id)
        try:
            clean = form.validate(answers, self.fields_for(case_id, slot),
                                  rnd.agreed if slot == "3" else None)  # fmt: skip
        except FormRefused as exc:
            raise Flow3Refused(str(exc)) from exc
        user = self._user(user_id, "adjudicator")
        base = f"adjudication/round{rnd.number}/pass{rnd.passes}"
        rel = f"{base}/review{slot}_{user_id}.json"
        sha = sha256_bytes(json_bytes(clean))  # signed first: a wrong password writes nothing
        records = [{"name": "answers", "path": rel, "sha256": sha},
                   {"name": "form", **{k: rnd.form[k] for k in ("path", "sha256")}},
                   {"name": "flow3 manifest", **rnd.manifest}]  # fmt: skip
        meaning = ADJ3_MEANING if slot == "3" else REVIEW_MEANING
        signature = sign(user, password, meaning, records)

        def still_open(prior: list[dict[str, Any]]) -> None:  # another tab, a reassignment
            now = round_of(prior)
            if (now is None or (now.number, now.passes) != (rnd.number, rnd.passes)
                    or _slot(now, replay(prior).state, user_id) != slot):  # fmt: skip
                raise Flow3Refused("you have no open review on this case")
            if slot in now.signed:
                raise Flow3Refused("you have already signed this review")

        try:
            self.store.put_json_once(case_id, rel, clean)
        except FileExistsError:  # another tab signed different answers first
            still_open(self.store.events(case_id))
            raise
        self._event(case_id, "adj3.signed" if slot == "3" else "adjudication.signed", user_id,
                    {"round": rnd.number, "pass": rnd.passes, "slot": slot,
                     "answers": {"path": rel, "sha256": sha}},
                    signature=signature, expect=still_open)  # fmt: skip

    def request_more_data(self, case_id: str, user_id: str, password: str, text: str) -> None:
        """An assigned adjudicator during review, or the Coordinator for the committee
        (ADJ-05, ADJ-13), signed: like every query, it reaches the site only on a person's
        signature (ADR 0001). The open reviews pause; the site gets a query."""
        text = text.strip()
        if not text:
            raise Flow3Refused("say what is needed")
        state = self._state(case_id)
        if state == State.ADJ_REVIEW:
            if self.slot_of(case_id, user_id) is None:
                raise Flow3Refused("only an assigned adjudicator can ask for more data")
            source, user = "review", self._user(user_id, "adjudicator")
        elif state == State.CEC_MEETING:
            source, user = "committee", self._user(user_id, "cec_coordinator")
        else:
            raise Flow3Refused("more data can be asked for during review or by the committee")
        rnd = current_round(self.store, case_id)
        request = {"from": source, "text": text, "round": rnd.number, "pass": rnd.passes}
        n = sum(e["type"] == "more_data.requested" for e in self.store.events(case_id)) + 1
        rel = f"adjudication/round{rnd.number}/more_data_v{n}.json"
        sha = sha256_bytes(json_bytes(request))  # signed first: a wrong password writes nothing
        signature = sign(user, password, MORE_DATA_MEANING,
                         [{"name": "request", "path": rel, "sha256": sha}])  # fmt: skip
        self.store.put_json_once(case_id, rel, request)
        self._event(case_id, "more_data.requested", user_id,
                    {**request, "request": {"path": rel, "sha256": sha}},
                    signature=signature)  # fmt: skip
        self._notify(case_id, "role:cec_coordinator", "more_data_requested", user_id)

    # -- CEC Coordinator and Chair ------------------------------------------------------------

    def reassign(self, case_id: str, coordinator_id: str, slot: str, to: str,
                 reason: str, expected_from: str | None = None) -> None:  # fmt: skip
        """Move an open review to another eligible adjudicator (ASN-02, ASN-07).
        `expected_from` is the holder the Coordinator saw: a page left open must not move
        another person's review."""
        self._user(coordinator_id, "cec_coordinator")
        if not reason.strip():
            raise Flow3Refused("give a reason for the reassignment")
        rnd = current_round(self.store, case_id)
        state = self._state(case_id)

        def check(now: Round | None, state: State) -> None:
            if now is None or slot not in _open_slots(state) or slot in now.signed:
                raise Flow3Refused("that review is not open")
            if expected_from is not None and now.holders.get(slot) != expected_from:
                raise Flow3Refused("that review has changed; choose again")
            if to in now.holders.values():
                raise Flow3Refused("that person already holds a review on this case")

        check(rnd, state)
        if to not in self.pool(case_id):
            raise Flow3Refused("that person may not review this case")

        def unchanged(prior: list[dict[str, Any]]) -> None:  # a signature, another reassignment
            now = round_of(prior)
            check(now, replay(prior).state)
            if ((now.number, now.passes) != (rnd.number, rnd.passes)
                    or now.holders[slot] != rnd.holders[slot]):  # fmt: skip
                raise Flow3Refused("that review is not open")

        self._event(case_id, "adjudication.reassigned", coordinator_id,
                    {"round": rnd.number, "pass": rnd.passes, "slot": slot,
                     "from": rnd.holders[slot], "to": to}, unchanged,
                    reason=reason.strip())  # fmt: skip
        self._notify(
            case_id,
            f"user:{to}",
            "adj3_assigned" if slot == "3" else "adjudication_assigned",
            coordinator_id,
        )

    def committee_fields(self, case_id: str) -> list[str]:
        return current_round(self.store, case_id).unresolved

    def record_meeting(self, case_id: str, coordinator_id: str, *, held_on: str,
                       attendees: list[str], summary: str,
                       decisions: dict[str, Any]) -> None:  # fmt: skip
        """The meeting is held outside the tool and recorded here (ADJ-10, Q-14)."""
        self._user(coordinator_id, "cec_coordinator")
        if self._state(case_id) != State.CEC_MEETING:
            raise Flow3Refused("the case is not waiting for the committee")
        try:
            held = date.fromisoformat(held_on)
        except ValueError as exc:
            raise Flow3Refused("give the meeting date") from exc
        if held > self.store.clock().astimezone().date():
            raise Flow3Refused("the meeting date cannot be in the future")
        members = {u for u, v in self.users().items() if v["role"] == "adjudicator"}
        attendees = [a for a in attendees if a in members]
        if not summary.strip():
            raise Flow3Refused("write a short summary of the discussion")
        rnd = current_round(self.store, case_id)
        try:
            clean = self.form(case_id).validate(decisions, rnd.unresolved, rnd.agreed)
        except FormRefused as exc:
            raise Flow3Refused(str(exc)) from exc
        quorum = int(self.cfg.get("quorum", 3))
        meeting = {"held_on": held.isoformat(), "attendees": attendees, "quorum": quorum,
                   "quorum_met": len(attendees) >= quorum and self.chair() in attendees,
                   "summary": summary.strip(), "decisions": clean}  # fmt: skip
        n = sum(e["type"] == "meeting.recorded" for e in self.store.events(case_id)) + 1
        rel = f"adjudication/round{rnd.number}/meeting_v{n}.json"
        sha = self.store.put_json(case_id, rel, meeting)
        self._event(case_id, "meeting.recorded", coordinator_id,
                    {"round": rnd.number, "meeting": {"path": rel, "sha256": sha},
                     "quorum_met": meeting["quorum_met"]})  # fmt: skip
        if meeting["quorum_met"]:
            self._notify(case_id, f"user:{self.chair()}", "consensus_to_sign", coordinator_id)

    def sign_consensus(self, case_id: str, user_id: str, password: str, *,
                       shown: dict[str, Any] | None) -> None:  # fmt: skip
        """The Chair signs the committee's decisions as recorded (ADJ-12): `shown` is the
        meeting record (path and sha256) on his screen, refused if a newer one exists."""
        if user_id != self.chair():
            raise SignatureRefused("only the committee Chair can sign the consensus")
        rnd = current_round(self.store, case_id)
        if self._state(case_id) != State.CEC_MEETING or rnd.meeting is None:
            raise Flow3Refused("no meeting has been recorded yet")
        last = rnd.meeting
        resumed = [e for e in self.store.events(case_id) if e["type"] == "committee.resumed"]
        if resumed and resumed[-1]["seq"] > last["seq"]:
            raise Flow3Refused("record the meeting on the updated dossier first")
        if not last["data"]["quorum_met"]:
            raise Flow3Refused("the recorded meeting had no quorum")
        ref = last["data"]["meeting"]
        if ref != shown:
            raise Flow3Refused(MEETING_CHANGED)
        user = self.users()[user_id]
        signature = sign(user, password, CONSENSUS_MEANING, [{"name": "meeting", **ref}])

        def same_meeting(prior: list[dict[str, Any]]) -> None:
            if replay(prior).state != State.CEC_MEETING:  # signed in another tab meanwhile
                raise Flow3Refused("the case is no longer waiting for the committee")
            recorded = [e for e in prior if e["type"] == "meeting.recorded"]
            if recorded[-1]["data"]["meeting"] != ref:
                raise Flow3Refused(MEETING_CHANGED)

        data = {"round": rnd.number, "meeting": ref}
        draft = EventDraft(type="consensus.signed", actor=user_id, data=data, signature=signature)
        self.store.append_event(case_id, draft, expect=same_meeting)  # checked under the lock
