"""Flow 2: quality check and narrative (plan steps 13–17, autonomy model in plan §2).

Automatic steps, run by the runner:
    start_qc        SUBMITTED → QC_CHECKS, reading only the latest Flow 2 manifest
    run_qc          checks (+ QC agent) → qc.passed | query.opened | qc.review_needed
    run_narrative   draft (template, or the narrative agent) → narrative.approved |
                    narrative.review_needed

Human actions (Safety Specialist, signed; only on exceptions):
    sign_qc_pass, open_query, sign_narrative

A query sends the case back to the site; its answer goes through Flow 1 again
(upload → de-identification → site review → signature → new manifest → QC again).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adjud.events import EventDraft
from adjud.handoff import latest_manifest_ref
from adjud.notify import Outbox
from adjud.signatures import SignatureRefused, load_users, sign
from adjud.store import FileCaseStore, json_bytes
from adjud.workflow import State

SYSTEM_QC = "system:qc"
SYSTEM_NARRATIVE = "system:narrative"
QC_PASS_MEANING = "Quality check passed — the documents are complete and fit for adjudication"  # noqa: S105
QUERY_MEANING = "Quality check failed — a query is sent to the site"
NARRATIVE_MEANING = "Narrative approved — a factual summary for the adjudicators"
QUERY_CODES = {
    "missing_document": "A required document is missing",
    "wrong_patient": "A document seems to be about another patient",
    "wrong_event": "A document does not match this event",
    "unreadable": "A page cannot be read",
    "identifiers_left": "An identifier is still visible",
    "other": "Other",
}


class Flow2Refused(Exception):
    pass


def _rounds(store: FileCaseStore, case_id: str, event_type: str) -> int:
    return sum(e["type"] == event_type for e in store.events(case_id))


def _site(store: FileCaseStore, case_id: str) -> str:
    subject = store.state(case_id).subject or "--"
    return f"site:{subject.split('-')[1]}"


def _latest(store: FileCaseStore, case_id: str, event_type: str) -> dict[str, Any] | None:
    return next((e for e in reversed(store.events(case_id)) if e["type"] == event_type), None)


def _safety_specialist(config_dir: Path, user_id: str) -> dict[str, Any]:
    user = load_users(config_dir).get(user_id)
    if user is None or user["role"] != "safety_specialist":
        raise SignatureRefused("only the Safety Specialist can sign this")
    return user


@dataclass
class Flow2:
    store: FileCaseStore
    config_dir: Path
    study: str = "DEMO01"
    outbox: Outbox = field(init=False)

    def __post_init__(self) -> None:
        self.outbox = Outbox(self.store.paths, self.study, clock=self.store.clock)

    def _event(self, case_id: str, type_: str, actor: str, data: dict[str, Any],
               **kw: Any) -> None:  # fmt: skip
        self.store.append_event(case_id, EventDraft(type=type_, actor=actor, data=data, **kw))

    def _notify(self, case_id: str, to: str, template: str, actor: str) -> None:
        note = self.outbox.queue(case_id, to, template)
        self._event(case_id, "notification.queued", actor,
                    {"to": to, "template": template, "id": note["id"]})  # fmt: skip

    # -- automatic steps ---------------------------------------------------------------------

    def pending_qc_starts(self) -> list[str]:
        """SUBMITTED cases whose latest submission has been handed off."""
        out = []
        for cs in self.store.list_case_states(State.SUBMITTED):
            types = [e["type"] for e in self.store.events(cs.case_id)]
            signed = max(i for i, t in enumerate(types) if t == "submission.signed")
            if "handoff.flow2_ready" in types[signed:]:
                out.append(cs.case_id)
        return out

    def start_qc(self, case_id: str) -> None:
        ref = latest_manifest_ref(self.store, case_id)
        if ref is None:
            raise Flow2Refused(f"{case_id} has no Flow 2 manifest yet")
        self._event(case_id, "qc.started", SYSTEM_QC, {"manifest": ref})

    def run_qc(self, case_id: str) -> dict[str, Any]:
        """One QC round: deterministic checks (step 14) and the QC agent (step 15) decide
        between passing, querying the site and asking the Safety Specialist."""
        n = _rounds(self.store, case_id, "qc.checks_completed") + 1
        manifest = _latest(self.store, case_id, "qc.started")["data"]["manifest"]
        result = {
            "round": n,
            "manifest": manifest,
            "checks": [],
            "decision": "review",
            "reasons": ["Automatic checks are not available yet: the Safety Specialist decides"],
        }
        rel = f"qc/checks_v{n}.json"
        sha = self.store.put_file(case_id, rel, json_bytes(result))
        self._event(case_id, "qc.checks_completed", SYSTEM_QC,
                    {"round": n, "results": {"path": rel, "sha256": sha}})  # fmt: skip
        self._event(case_id, "qc.review_needed", SYSTEM_QC, {"reasons": result["reasons"]})
        self._notify(case_id, "role:safety_specialist", "qc_review_needed", SYSTEM_QC)
        return result

    def run_narrative(self, case_id: str) -> str:
        """Draft the narrative. With AI off (or its draft failing the gate) the study
        template is filled from the EDC record and the Safety Specialist reviews it."""
        n = _rounds(self.store, case_id, "narrative.drafted") + 1
        text = self.template_draft(case_id)
        rel = f"narrative/draft_v{n}.md"
        sha = self.store.put_file(case_id, rel, text.encode())
        self._event(case_id, "narrative.drafted", SYSTEM_NARRATIVE,
                    {"draft": {"path": rel, "sha256": sha}, "source": "template"})  # fmt: skip
        reasons = ["AI is off: the Safety Specialist completes the narrative"]
        self._event(case_id, "narrative.review_needed", SYSTEM_NARRATIVE, {"reasons": reasons})
        self._notify(case_id, "role:safety_specialist", "narrative_review_needed",
                     SYSTEM_NARRATIVE)  # fmt: skip
        return text

    def template_draft(self, case_id: str) -> str:
        manifest_ref = _latest(self.store, case_id, "qc.started")["data"]["manifest"]
        manifest = self.store.get_json(case_id, manifest_ref["path"])
        ae = self.store.get_json(case_id, manifest["ae_snapshot"]["path"])
        serious = ae.get("serious") == "Y"
        values = {
            "case_id": case_id,
            "subject_id": ae.get("subject_id", ""),
            "site_id": ae.get("site_id", ""),
            "ae_term": ae.get("ae_term", ""),
            "onset_date": ae.get("onset_date", ""),
            "end": f" and end on {ae['end_date']}" if ae.get("end_date") else "",
            "seriousness": (
                f"serious ({ae.get('seriousness_criteria', '').replace('_', ' ').lower()})"
                if serious
                else "not serious"
            ),
            "severity": (ae.get("severity") or "not recorded").replace("_", " ").lower(),
            "outcome": (ae.get("outcome") or "not recorded").replace("_", " ").lower(),
            "documents": ", ".join(d["source_name"] for d in manifest["documents"]) or "none",
        }
        template = (self.config_dir / self.study / "narrative_template.md").read_text()
        return template.format_map(values)

    # -- Safety Specialist actions -----------------------------------------------------------

    def _checks_ref(self, case_id: str) -> dict[str, str]:
        e = _latest(self.store, case_id, "qc.checks_completed")
        if e is None:
            raise Flow2Refused("no QC results yet")
        return e["data"]["results"]

    def sign_qc_pass(self, case_id: str, user_id: str, password: str,
                     reason: str | None = None) -> None:  # fmt: skip
        if self.store.state(case_id).state != State.QC_REVIEW:
            raise Flow2Refused("the case is not waiting for a QC review")
        user = _safety_specialist(self.config_dir, user_id)
        checks = self._checks_ref(case_id)
        manifest = _latest(self.store, case_id, "qc.started")["data"]["manifest"]
        records = [{"name": "qc results", **checks}, {"name": "flow2 manifest", **manifest}]
        signature = sign(user, password, QC_PASS_MEANING, records)
        self._event(case_id, "qc.signed_pass", user_id, {"results": checks},
                    signature=signature, reason=(reason or "").strip() or None)  # fmt: skip

    def open_query(self, case_id: str, codes: list[str], text: str, *,
                   user_id: str | None = None, password: str | None = None) -> None:  # fmt: skip
        """A query to the site: signed by the Safety Specialist from QC_REVIEW, or opened by
        the QC step itself when a required check fails for a clear reason."""
        codes = [c for c in codes if c in QUERY_CODES]
        if not codes:
            raise Flow2Refused("choose at least one reason for the query")
        state = self.store.state(case_id).state
        data = {"codes": codes, "text": text.strip(), "results": self._checks_ref(case_id)}
        if user_id is None:
            if state != State.QC_CHECKS:
                raise Flow2Refused("an automatic query is only possible during the checks")
            self._event(case_id, "query.opened", SYSTEM_QC, data)
            actor = SYSTEM_QC
        else:
            if state != State.QC_REVIEW:
                raise Flow2Refused("the case is not waiting for a QC review")
            user = _safety_specialist(self.config_dir, user_id)
            signature = sign(user, password or "", QUERY_MEANING,
                             [{"name": "qc results", **data["results"]}])  # fmt: skip
            self._event(case_id, "query.opened", user_id, data, signature=signature)
            actor = user_id
        self._notify(case_id, _site(self.store, case_id), "query_opened", actor)

    def sign_narrative(self, case_id: str, user_id: str, password: str, text: str) -> None:
        if self.store.state(case_id).state != State.NARRATIVE_REVIEW:
            raise Flow2Refused("the case is not waiting for a narrative review")
        if not text.strip():
            raise Flow2Refused("the narrative is empty")
        user = _safety_specialist(self.config_dir, user_id)
        n = _rounds(self.store, case_id, "narrative.signed") + 1
        draft = _latest(self.store, case_id, "narrative.drafted")["data"]
        rel = f"narrative/narrative_v{n}.md"
        sha = self.store.put_file(case_id, rel, text.strip().encode() + b"\n")
        signature = sign(user, password, NARRATIVE_MEANING,
                         [{"name": "narrative", "path": rel, "sha256": sha}])  # fmt: skip
        edited = self.store.get_file(case_id, draft["draft"]["path"]).decode().strip() != (
            text.strip()
        )
        self._event(case_id, "narrative.signed", user_id,
                    {"narrative": {"path": rel, "sha256": sha}, "source": draft["source"],
                     "edited": edited}, signature=signature)  # fmt: skip


def query_of(store: FileCaseStore, case_id: str) -> dict[str, Any] | None:
    """The open query's data, for the site's page, or None."""
    if store.state(case_id).state != State.QUERY_OPEN:
        return None
    e = _latest(store, case_id, "query.opened")
    return e["data"] if e else None
