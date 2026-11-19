"""Flow 2: the Quality Check and the narrative (plan steps 13–17, P-37, P-39).

Automatic steps, run by the runner:
    start_qc        SUBMITTED → QC_CHECKS, reading only the latest Flow 2 manifest
    run_qc          checks, the AI Suggestion, the dossier and the Narrative Draft, all
                    before the Safety Specialist looks → qc.review_needed (QC_REVIEW)

Human actions (Safety Specialist, signed, on every case; ADR 0001):
    sign_qc_pass    one signature: the QC pass and the edited narrative → ready
    open_query      a Site Query → QUERY_OPEN

Cases from before P-39 may still sit in NARRATIVE or NARRATIVE_REVIEW: `run_narrative`
and `sign_narrative` finish them the old way.

A query sends the case back to the site; its answer goes through Flow 1 again
(upload → de-identification → site review → signature → new manifest → QC again).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adjud.ai.functions import DOCUMENT_TYPE, NARRATIVE, QC_SUGGESTION
from adjud.ai.gates import GateResult, gate
from adjud.ai.gateway import AiResult, Gateway
from adjud.ai.tools import case_toolbox, dossier_page_text, output_pages, page_text
from adjud.dossier import build_and_record, latest_dossier
from adjud.events import EventDraft, sha256_bytes
from adjud.handoff import latest_manifest_ref
from adjud.notify import Outbox
from adjud.qc import Checklist, run_checks
from adjud.signatures import SignatureRefused, load_users, sign
from adjud.store import FileCaseStore, json_bytes
from adjud.suggestion import assess, event_from_fields, suggested_outcome
from adjud.workflow import State, replay

SYSTEM_QC = "system:qc"
SYSTEM_NARRATIVE = "system:narrative"
QC_PASS_MEANING = "Quality check passed — the documents are complete and fit for adjudication"  # noqa: S105
QUERY_MEANING = "Quality check failed — a query is sent to the site"
NARRATIVE_MEANING = "Narrative approved — a factual summary for the adjudicators"
REVIEW_MEANING = (
    "Quality check passed — the documents are complete and fit for adjudication — "
    "and narrative approved — a factual summary for the adjudicators"
)
APPROVALS = ("narrative.approved", "narrative.signed", "quality_check.signed")
QUERY_CODES = {
    "missing_document": "More documents needed",
    "wrong_patient": "A document seems to be about another patient",
    "wrong_event": "Documents don't match the EDC event",
    "unreadable": "A page cannot be read",
    "identifiers_left": "An identifier is still visible",
    "other": "Other",
    "more_data": "More information is needed for the adjudication",  # Flow 3 (P-47)
}
QC_QUERY_CODES = [c for c in QUERY_CODES if c != "more_data"]  # the Safety Specialist's


class Flow2Refused(Exception):
    pass


def _rounds(store: FileCaseStore, case_id: str, event_type: str) -> int:
    return sum(e["type"] == event_type for e in store.events(case_id))


def _site(store: FileCaseStore, case_id: str) -> str:
    subject = store.state(case_id).subject or "--"
    return f"site:{subject.split('-')[1]}"


def _latest(store: FileCaseStore, case_id: str, event_type: str) -> dict[str, Any] | None:
    return next((e for e in reversed(store.events(case_id)) if e["type"] == event_type), None)


def _still_in(state: State, message: str) -> Callable[[list[dict[str, Any]]], None]:
    """An `expect` for a signature: refused if another tab moved the case on meanwhile."""

    def check(prior: list[dict[str, Any]]) -> None:
        if replay(prior).state != state:
            raise Flow2Refused(message)

    return check


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
    gateway: Gateway | None = None  # from study.yaml when not given
    outbox: Outbox = field(init=False)
    checklist: Checklist = field(init=False)
    narrative_cfg: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.outbox = Outbox(self.store.paths, self.study, clock=self.store.clock)
        self.checklist = Checklist.for_study(self.config_dir, self.study)
        study_cfg = yaml.safe_load((self.config_dir / self.study / "study.yaml").read_text())
        self.narrative_cfg = {"min_statements": 3, "forbidden": [],
                              **(study_cfg.get("narrative") or {})}  # fmt: skip
        if self.gateway is None:
            self.gateway = Gateway.for_study(self.store, self.config_dir, self.study)

    def _event(self, case_id: str, type_: str, actor: str, data: dict[str, Any],
               expect: Callable[[list[dict[str, Any]]], None] | None = None,
               **kw: Any) -> None:  # fmt: skip
        """Append one event; `expect(prior events)` re-checks under the case lock."""
        self.store.append_event(case_id, EventDraft(type=type_, actor=actor, data=data, **kw),
                                expect=expect)  # fmt: skip

    def _put_signed(self, case_id: str, rel: str, content: bytes,
                    expect: Callable[[list[dict[str, Any]]], None]) -> None:  # fmt: skip
        """Write a file about to be signed. Another tab that signed first wrote a different
        file of that name: say the case moved on rather than fail on the file."""
        try:
            self.store.put_file_once(case_id, rel, content)
        except FileExistsError:
            expect(self.store.events(case_id))
            raise

    def _notify(
        self, case_id: str, to: str, template: str, actor: str, page: str | None = None
    ) -> None:
        note = self.outbox.queue(case_id, to, template, page=page)
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
        """One QC round, prepared for the Safety Specialist (P-37, P-39). Code runs the
        checks; the document-type agent types what code could not; the `qc_suggestion`
        agent gives its Findings on every case; code derives the Suggested Outcome (pass,
        query, or none when anything is unclear); the dossier is built and the narrative
        drafted. The case then waits at one stop, QC review, where the Safety Specialist
        reviews it all and signs the pass with the narrative, or a Site Query."""
        n = _rounds(self.store, case_id, "qc.checks_completed") + 1
        manifest_ref = _latest(self.store, case_id, "qc.started")["data"]["manifest"]
        manifest = self.store.get_json(case_id, manifest_ref["path"])
        ae = self.store.get_json(case_id, manifest["ae_snapshot"]["path"])
        outcome = run_checks(self.store, case_id, manifest, self.checklist)
        # Failures found by code alone: a signed pass is refused while any is present.
        failed_by_code = [c.id for c in outcome.failed]
        ai: list[dict[str, Any]] = []
        ai_types = self._type_agent(case_id, manifest, outcome, ai)
        if ai_types:
            outcome = run_checks(self.store, case_id, manifest, self.checklist,
                                 ai_types=ai_types)  # fmt: skip
        suggestion = self._suggestion_agent(case_id, manifest, outcome, ae, ai)
        onset = next(c for c in outcome.checks if c.id == "subject_and_onset")
        if onset.status == "unknown" and (event := event_from_fields(suggestion)):
            outcome = run_checks(self.store, case_id, manifest, self.checklist,
                                 ai_types=ai_types, event=event)  # fmt: skip
        checks = outcome.as_dicts()
        proposal, reasons, codes = suggested_outcome(checks, suggestion)
        if proposal == "unclear":
            reasons += [f"{a['task']} for {a['about']}: {r}" for a in ai
                        if a["task"] == "Document type"
                        for r in (a["gate"] or {}).get("reasons", [])]  # fmt: skip
        dossier = build_and_record(self.store, case_id, manifest_ref, outcome.types,
                                   self.config_dir, self.study)  # fmt: skip
        narrative = self._draft_narrative(case_id, dossier)
        result = {
            "round": n,
            "manifest": manifest_ref,
            "checks": checks,
            "types": outcome.types,
            "decision": "review",  # always the Safety Specialist's (P-37, ADR 0001)
            "proposal": proposal,  # the Suggested Outcome: pass | query | unclear
            "query_codes": codes,
            "failed_by_code": failed_by_code,
            "reasons": reasons,
            "suggestion": suggestion,
            "dossier": {"version": dossier["version"], **dossier["dossier"]},
            "narrative": narrative,
            "ai": ai,
        }
        rel = f"qc/checks_v{n}.json"
        sha = self.store.put_file(case_id, rel, json_bytes(result))
        self._event(case_id, "qc.checks_completed", SYSTEM_QC,
                    {"round": n, "results": {"path": rel, "sha256": sha},
                     "decision": "review", "proposal": proposal})  # fmt: skip
        self._event(case_id, "qc.review_needed", SYSTEM_QC,
                    {"reasons": reasons, "proposal": proposal})  # fmt: skip
        self._notify(case_id, "role:safety_specialist", "qc_review_needed", SYSTEM_QC, "qc")
        return result

    # -- QC agents ---------------------------------------------------------------------------

    def _pages_of(self, case_id: str, manifest: dict[str, Any]) -> dict[str, int]:
        return {d["source_name"]: len(output_pages(self.store, case_id, d["path"]))
                for d in manifest["documents"]}  # fmt: skip

    def _type_agent(
        self, case_id: str, manifest: dict[str, Any], outcome: Any, log: list[dict[str, Any]]
    ) -> dict[str, str]:
        """Ask the document-type agent about each document code could not type; keep
        only gated answers."""
        assert self.gateway is not None
        cfg, toolbox = self.gateway.cfg, case_toolbox(self.store, case_id)
        pages_of = self._pages_of(case_id, manifest)

        def text_of(file: str, page: int) -> str | None:
            return page_text(self.store, case_id, file, page)

        allowed = list(self.checklist.types)
        ai_types: dict[str, str] = {}
        for name, typed in outcome.types.items():
            if typed["type"] != "unknown":
                continue
            payload = {"file": name, "pages": pages_of[name], "allowed_types": allowed}
            r = self.gateway.run(case_id, DOCUMENT_TYPE, payload, toolbox)
            out = r.output or {}
            evidence = out.get("evidence") or {}
            g = None
            if r.status != "unavailable":
                g = gate(r, min_confidence=cfg.min_confidence, page_text=text_of,
                         citations=[evidence] if evidence else [],
                         checks=[(f"type {out.get('document_type')!r} is not on the list",
                                  out.get("document_type") in allowed),
                                 ("the quote is from another file",
                                  evidence.get("file") == name)])  # fmt: skip
                if g.passed:
                    ai_types[name] = out["document_type"]
            answer = self.checklist.label(out["document_type"]) if out else ""
            self._log_ai(case_id, log, "Document type", name, r, g, answer)
        return ai_types

    def _suggestion_agent(
        self,
        case_id: str,
        manifest: dict[str, Any],
        outcome: Any,
        ae: dict[str, Any],
        log: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """The AI Suggestion (P-39): the agent's Findings, each checked by code, plus the
        Required Documents finding, which is code's."""
        assert self.gateway is not None
        texts = {d["source_name"]: output_pages(self.store, case_id, d["path"])
                 for d in manifest["documents"]}  # fmt: skip
        required = self.checklist.required_for(ae)
        present = {t["type"] for t in outcome.types.values()}
        payload = {
            "ae": {k: ae.get(k) for k in ("ae_term", "onset_date", "end_date", "outcome",
                                          "serious", "seriousness_criteria")},
            # Names, pages and types, not the text: a recorded answer still replays after
            # the site's redaction edits or another OCR build; its quotes are checked
            # against the real text either way, so a changed passage is never "verified"
            "documents": [{"file": n, "pages": len(texts[n]), "type": outcome.types[n]["type"]}
                          for n in sorted(texts)],
            "required_types": required,
            "missing_required_types": [t for t in required if t not in present],
            "allowed_types": list(self.checklist.types),
        }  # fmt: skip
        r = self.gateway.run(case_id, QC_SUGGESTION, payload, case_toolbox(self.store, case_id))

        def text_of(file: str, page: int) -> str | None:
            return page_text(self.store, case_id, file, page)

        suggestion = assess(r, ae, page_text=text_of,
                            min_confidence=self.gateway.cfg.min_confidence,
                            allowed_types=list(self.checklist.types),
                            present_types=present)  # fmt: skip
        req = next(c for c in outcome.checks if c.id == "required_documents")
        suggestion["documents"] = {
            "status": {"passed": "ok", "failed": "problem"}.get(req.status, "not_sure"),
            "detail": req.detail,
            "required": [self.checklist.label(t) for t in required],
            "would_help": [{**d, "label": self.checklist.label(d["type"])}
                           for d in suggestion["would_help"]],
        }  # fmt: skip
        g = None
        if r.status != "unavailable":
            problems = [f"{f['label']}: {p}" for f in suggestion["fields"] for p in f["problems"]]
            if not r.ok:
                problems.insert(0, f"AI result not usable ({r.note or r.status})")
            g = GateResult(passed=not problems, reasons=problems)
        self._log_ai(case_id, log, "AI Suggestion", "all documents", r, g, suggestion["summary"])
        return suggestion

    def _log_ai(self, case_id: str, log: list[dict[str, Any]], task: str, about: str,
                r: AiResult, g: GateResult | None, answer: str) -> None:  # fmt: skip
        log.append({
            "task": task, "about": about, "function": r.function, "status": r.status,
            "label": r.label, "note": r.note, "answer": answer, "call": r.call,
            "gate": g.as_dict() if g else None,
        })  # fmt: skip
        if g is not None:
            self._event(case_id, "ai.gate", SYSTEM_QC,
                        {"function": r.function, "about": about, "call": r.call,
                         **g.as_dict()})  # fmt: skip

    def ensure_dossier(self, case_id: str) -> dict[str, Any]:
        """Build the dossier for the latest passed QC round, once (step 16)."""
        types_ = [e["type"] for e in self.store.events(case_id)]
        passed = max(i for i, t in enumerate(types_) if t in ("qc.passed", "qc.signed_pass"))
        if "dossier.built" in types_[passed:]:
            return latest_dossier(self.store, case_id)
        checks = self.store.get_json(case_id, self._checks_ref(case_id)["path"])
        manifest = _latest(self.store, case_id, "qc.started")["data"]["manifest"]
        return build_and_record(self.store, case_id, manifest, checks.get("types", {}),
                                self.config_dir, self.study)  # fmt: skip

    def _draft_narrative(self, case_id: str, dossier: dict[str, Any]) -> dict[str, Any]:
        """Draft the narrative from the dossier (step 17): the narrative agent writes
        facts-only statements, each quoting a dossier page, and code checks every quote;
        when AI is off or its answer is unusable, the study template is filled in. A
        person always edits and signs it. Returns the draft's reference and status."""
        n = _rounds(self.store, case_id, "narrative.drafted") + 1
        r, g, statements = self._narrative_agent(case_id, dossier)
        if statements:
            text = "\n".join(f"{s['text']} [dossier p. {s['page']}]" for s in statements)
            source = "ai"
        else:
            text, source = self.template_draft(case_id), "template"
        rel = f"narrative/draft_v{n}.md"
        sha = self.store.put_file(case_id, rel, text.encode())
        if g is not None and g.passed:
            reasons: list[str] = []  # nothing wrong with the draft; a person still signs it
        elif r.status == "unavailable":
            reasons = [f"No AI draft ({r.note or 'AI is off'}): the Safety Specialist completes "
                       "the narrative"]  # fmt: skip
        else:
            reasons = [f"AI draft not accepted: {x}" for x in (g.reasons if g else [r.note])]
        draft = {"draft": {"path": rel, "sha256": sha}, "source": source,
                 "simulated": r.simulated, "call": r.call}  # fmt: skip
        self._event(case_id, "narrative.drafted", SYSTEM_NARRATIVE, draft)
        return {**draft, "label": r.label, "gate_passed": bool(g and g.passed),
                "reasons": reasons}  # fmt: skip

    def run_narrative(self, case_id: str) -> str:
        """Cases from before P-39 only, sitting in NARRATIVE after a signed QC pass: build
        the dossier, draft the narrative and send it to narrative review."""
        dossier = self.ensure_dossier(case_id)
        d = self._draft_narrative(case_id, dossier)
        self._event(case_id, "narrative.review_needed", SYSTEM_NARRATIVE,
                    {"reasons": d["reasons"], "gate_passed": d["gate_passed"]})  # fmt: skip
        self._notify(case_id, "role:safety_specialist", "narrative_review_needed",
                     SYSTEM_NARRATIVE)  # fmt: skip
        return self.store.get_file(case_id, d["draft"]["path"]).decode()

    def _narrative_agent(
        self, case_id: str, dossier: dict[str, Any]
    ) -> tuple[AiResult, GateResult | None, list[dict[str, Any]]]:
        """Ask the narrative agent; returns its result, the gate, and its statements (empty
        when it gave none)."""
        assert self.gateway is not None
        manifest = self.store.get_json(case_id, dossier["manifest"]["path"])
        ae = self.store.get_json(case_id, manifest["ae_snapshot"]["path"])
        payload = {
            "ae": {k: ae.get(k) for k in ("ae_term", "onset_date", "end_date", "serious",
                                          "seriousness_criteria", "outcome", "severity")},
            "dossier": {"version": dossier["version"], "page_map": dossier["page_map"]},
        }  # fmt: skip
        r = self.gateway.run(case_id, NARRATIVE, payload, case_toolbox(self.store, case_id))
        if r.status == "unavailable":
            return r, None, []
        statements = (r.output or {}).get("statements") or []
        cfg = self.narrative_cfg

        def dossier_text(_file: str, page: int) -> str | None:
            return dossier_page_text(self.store, case_id, page)

        found = sorted({w for s in statements for w in cfg["forbidden"]
                        if w.lower() in s["text"].lower()})  # fmt: skip
        g = gate(r, min_confidence=self.gateway.cfg.min_confidence, page_text=dossier_text,
                 citations=[{"file": "dossier", "page": s["page"], "quote": s["quote"]}
                            for s in statements],
                 checks=[(f"fewer than {cfg['min_statements']} statements",
                          len(statements) >= cfg["min_statements"]),
                         (f"forbidden wording: {', '.join(found)}", not found)])  # fmt: skip
        self._event(case_id, "ai.gate", SYSTEM_NARRATIVE,
                    {"function": r.function, "about": "narrative", "call": r.call,
                     **g.as_dict()})  # fmt: skip
        return r, g, statements

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

    def pending_flow3_handoffs(self) -> list[str]:
        """READY_FOR_ADJUDICATION cases without a package for their latest approval: the
        first time, and again after more data went through the Quality Check (P-47)."""
        out = []
        for cs in self.store.list_case_states(State.READY_FOR_ADJUDICATION):
            types = [e["type"] for e in self.store.events(cs.case_id)]
            approved = max(i for i, t in enumerate(types) if t in APPROVALS)
            if "handoff.flow3_ready" not in types[approved:]:
                out.append(cs.case_id)
        return out

    def write_flow3_handoff(self, case_id: str) -> dict[str, Any]:
        """The package for Flow 3 (step 18): the dossier, the approved narrative and the QC
        results, each by path and SHA-256. Like the Flow 2 manifest, it never names
        anything from documents/original/. Written once; a re-run after a crash reuses an
        identical file."""
        approval = next(e for e in reversed(self.store.events(case_id))
                        if e["type"] in ("narrative.approved", "narrative.signed",
                                         "quality_check.signed"))  # fmt: skip
        dossier = latest_dossier(self.store, case_id)
        qc_pass = next(e for e in reversed(self.store.events(case_id))
                       if e["type"] in ("qc.passed", "qc.signed_pass",
                                        "quality_check.signed"))  # fmt: skip
        cs = self.store.state(case_id)
        manifest = {
            "case_id": case_id,
            "study": cs.study,
            "subject": cs.subject,
            "ae_id": cs.ae_id,
            "flow2_manifest": dossier["manifest"],
            "dossier": {**dossier["dossier"], "version": dossier["version"],
                        "page_map": dossier["page_map"]},
            "qc": {**qc_pass["data"]["results"], "passed_by": qc_pass["actor"],
                   "passed_at": qc_pass["at"]},
            "narrative": {
                **approval["data"]["narrative"],
                "source": approval["data"].get("source"),
                "approved_by": (approval["signature"]["signer"] if approval.get("signature")
                                else approval["actor"]),
                "approved_at": approval["at"],
                "simulated_ai": bool(approval["data"].get("simulated")),
            },
        }  # fmt: skip
        content = json_bytes(manifest)
        n = _rounds(self.store, case_id, "handoff.flow3_ready") + 1
        rel = "handoff/flow3_manifest.json" if n == 1 else f"handoff/flow3_manifest_v{n}.json"
        path = self.store.case_dir(case_id) / rel
        if path.exists():
            if path.read_bytes() != content:
                raise Flow2Refused(f"an existing {rel} differs; not overwriting")
            sha = sha256_bytes(content)
        else:
            sha = self.store.put_file(case_id, rel, content)
        self._event(case_id, "handoff.flow3_ready", SYSTEM_QC,
                    {"manifest": {"path": rel, "sha256": sha}})  # fmt: skip
        self._notify(case_id, "role:safety_specialist", "ready_for_adjudication", SYSTEM_QC)
        return manifest

    # -- Safety Specialist actions -----------------------------------------------------------

    def _checks_ref(self, case_id: str) -> dict[str, str]:
        e = _latest(self.store, case_id, "qc.checks_completed")
        if e is None:
            raise Flow2Refused("no QC results yet")
        return e["data"]["results"]

    def _check_narrative(self, text: str) -> None:
        if not text.strip():
            raise Flow2Refused("the narrative is empty")
        template = (self.config_dir / self.study / "narrative_template.md").read_text()

        # Any sentence of a [bracketed] note still there, brackets or not, counts (B25)
        def norm(s: str) -> str:
            return " ".join(s.casefold().split())

        body = norm(text)
        notes = re.findall(r"\[([^\]]+)\]", template)
        sentences = [norm(s).rstrip(".!?") for n in notes for s in re.split(r"(?<=[.!?])\s+", n)]
        if any(s and s in body for s in sentences):
            raise Flow2Refused(
                "complete the narrative first: the template's instruction in [brackets] "
                "is still in it"
            )

    def sign_qc_pass(self, case_id: str, user_id: str, password: str, narrative: str,
                     reason: str | None = None) -> None:  # fmt: skip
        """The Safety Specialist's one signature at the Quality Check (P-39): the QC pass
        and the narrative as edited, together. Refused while a check failed in code; a
        pass against a suggested query (an AI-found problem) needs a reason."""
        if self.store.state(case_id).state != State.QC_REVIEW:
            raise Flow2Refused("the case is not waiting for a QC review")
        checks = self._checks_ref(case_id)
        result = self.store.get_json(case_id, checks["path"])
        failed = result.get("failed_by_code") or []
        if failed:
            raise Flow2Refused(
                "a check failed (" + ", ".join(failed).replace("_", " ") + "): send a query "
                "to the site instead"
            )
        reason = (reason or "").strip() or None
        if result.get("proposal") == "query" and not reason:
            raise Flow2Refused("the suggested outcome is a query: give a reason for the pass")
        draft = result.get("narrative")
        if draft is None:
            raise Flow2Refused("this round has no narrative draft")
        self._check_narrative(narrative)
        user = _safety_specialist(self.config_dir, user_id)
        n = (
            _rounds(self.store, case_id, "quality_check.signed")
            + _rounds(self.store, case_id, "narrative.signed")
            + 1
        )
        rel = f"narrative/narrative_v{n}.md"
        content = narrative.strip().encode() + b"\n"
        sha = sha256_bytes(content)  # signed first: a wrong password writes nothing
        manifest = _latest(self.store, case_id, "qc.started")["data"]["manifest"]
        dossier = {k: result["dossier"][k] for k in ("path", "sha256")}
        records = [{"name": "qc results", **checks}, {"name": "flow2 manifest", **manifest},
                   {"name": "dossier", **dossier}, {"name": "narrative", "path": rel,
                                                     "sha256": sha}]  # fmt: skip
        signature = sign(user, password, REVIEW_MEANING, records)
        expect = _still_in(State.QC_REVIEW, "the case is not waiting for a QC review")
        self._put_signed(case_id, rel, content, expect)
        # What was edited: this round's draft, or what carried over from an earlier round
        # (the AI draft from before a query, or the draft a carried edit started from)
        carried = qc_drafts(self.store, case_id)
        base = carried.narrative_base or draft
        original = self.store.get_file(case_id, base["draft"]["path"]).decode().strip()
        data: dict[str, Any] = {
            "outcome": "pass", "results": checks,
            "dossier": {**dossier, "version": result["dossier"]["version"]},
            "narrative": {"path": rel, "sha256": sha}, "draft": base["draft"],
            "source": base["source"], "simulated": bool(base.get("simulated")),
            "edited": original != narrative.strip(),
        }  # fmt: skip
        if carried.narrative_base:
            data["carried_from_round"] = carried.narrative_round
        self._event(case_id, "quality_check.signed", user_id, data, expect,
                    signature=signature, reason=reason)  # fmt: skip

    def open_query(self, case_id: str, codes: list[str], text: str, *,
                   user_id: str, password: str) -> None:  # fmt: skip
        """A query to the site, always signed by the Safety Specialist from QC review
        (P-37): nothing goes back to the site on a machine's judgement alone."""
        codes = [c for c in codes if c in QUERY_CODES]
        if not codes:
            raise Flow2Refused("choose at least one reason for the query")
        if not text.strip():  # QC-07: reason codes plus free text for the site
            raise Flow2Refused("write the query text for the site")
        if self.store.state(case_id).state != State.QC_REVIEW:
            raise Flow2Refused("the case is not waiting for a QC review")
        data = {"codes": codes, "text": text.strip(), "results": self._checks_ref(case_id)}
        user = _safety_specialist(self.config_dir, user_id)
        signature = sign(user, password or "", QUERY_MEANING,
                         [{"name": "qc results", **data["results"]}])  # fmt: skip
        self._event(case_id, "query.opened", user_id, data,
                    _still_in(State.QC_REVIEW, "the case is not waiting for a QC review"),
                    signature=signature)  # fmt: skip
        self._notify(case_id, _site(self.store, case_id), "query_opened", user_id)

    # -- unsigned drafts at the Quality Check (P-58, Q4) ---------------------------------

    def _draft_round(self, case_id: str, user_id: str) -> tuple[int, dict[str, Any]]:
        """The QC round a draft belongs to, and its results. Only the Safety Specialist
        drafts, and only while the case waits for their review."""
        user = load_users(self.config_dir).get(user_id)
        if user is None or user["role"] != "safety_specialist":
            raise Flow2Refused("only the Safety Specialist drafts at the Quality Check")
        if self.store.state(case_id).state != State.QC_REVIEW:
            raise Flow2Refused("the case is not waiting for a QC review")
        result = self.store.get_json(case_id, self._checks_ref(case_id)["path"])
        return int(result["round"]), result

    def save_narrative_draft(self, case_id: str, user_id: str, text: str,
                             via: str | None = None) -> dict[str, Any]:  # fmt: skip
        """The narrative as the Safety Specialist edited it, unsigned (P-58, Q4): it
        pre-fills the Quality check page, where the one signature stays (P-39). The AI or
        template draft is kept as it was, so the signature still records the edit. Never
        moves the case."""
        rnd, result = self._draft_round(case_id, user_id)
        if result.get("narrative") is None:
            raise Flow2Refused("this round has no narrative draft")
        if not text.strip():
            raise Flow2Refused("the narrative is empty")
        n = _rounds(self.store, case_id, "narrative.draft_edited") + 1
        rel = f"narrative/edit_v{n}.md"
        sha = self.store.put_file(case_id, rel, text.strip().encode() + b"\n")
        data: dict[str, Any] = {"round": rnd, "draft": {"path": rel, "sha256": sha}}
        if via:
            data["via"] = via
        waiting = _still_in(State.QC_REVIEW, "the case is not waiting for a QC review")
        self._event(case_id, "narrative.draft_edited", user_id, data, waiting)
        return data

    def save_query_draft(self, case_id: str, user_id: str, codes: list[str], text: str,
                         via: str | None = None) -> dict[str, Any]:  # fmt: skip
        """A Site Query drafted, unsigned (P-58, Q4, Q13): it pre-fills the query on the
        Quality check page. Nothing reaches the site until it is signed there (ADR 0001)."""
        rnd, _ = self._draft_round(case_id, user_id)
        unknown = [c for c in codes if c not in QC_QUERY_CODES]
        if unknown:
            raise Flow2Refused(f"unknown query reasons: {', '.join(unknown)}")
        if not codes and not text.strip():
            raise Flow2Refused("the query draft is empty")
        data: dict[str, Any] = {"round": rnd, "codes": list(dict.fromkeys(codes)),
                                "text": text.strip()}  # fmt: skip
        if via:
            data["via"] = via
        waiting = _still_in(State.QC_REVIEW, "the case is not waiting for a QC review")
        self._event(case_id, "query.drafted", user_id, data, waiting)
        return data

    def sign_narrative(self, case_id: str, user_id: str, password: str, text: str) -> None:
        """Cases from before P-39, waiting in NARRATIVE_REVIEW."""
        if self.store.state(case_id).state != State.NARRATIVE_REVIEW:
            raise Flow2Refused("the case is not waiting for a narrative review")
        self._check_narrative(text)
        user = _safety_specialist(self.config_dir, user_id)
        n = _rounds(self.store, case_id, "narrative.signed") + 1
        draft = _latest(self.store, case_id, "narrative.drafted")["data"]
        rel = f"narrative/narrative_v{n}.md"
        content = text.strip().encode() + b"\n"
        sha = sha256_bytes(content)  # signed first: a wrong password writes nothing
        signature = sign(user, password, NARRATIVE_MEANING,
                         [{"name": "narrative", "path": rel, "sha256": sha}])  # fmt: skip
        expect = _still_in(State.NARRATIVE_REVIEW, "the case is not waiting for a narrative review")
        self._put_signed(case_id, rel, content, expect)
        edited = self.store.get_file(case_id, draft["draft"]["path"]).decode().strip() != (
            text.strip()
        )
        self._event(case_id, "narrative.signed", user_id,
                    {"narrative": {"path": rel, "sha256": sha}, "source": draft["source"],
                     "simulated": bool(draft.get("simulated")), "edited": edited},
                    expect, signature=signature)  # fmt: skip


def query_of(store: FileCaseStore, case_id: str) -> dict[str, Any] | None:
    """The open query's data, for the site's page, or None."""
    if store.state(case_id).state != State.QUERY_OPEN:
        return None
    e = _latest(store, case_id, "query.opened")
    return e["data"] if e else None


@dataclass(frozen=True)
class QcDrafts:
    """The latest unsigned drafts of the current QC round (P-58): what the Quality check
    page starts from instead of the AI or template draft and an empty query."""

    narrative: str | None = None
    narrative_by: str | None = None
    narrative_at: str | None = None
    # "slack" or "app": the Safety Specialist's edit (None: an edit from before P-59);
    # "earlier_ai": no edit, and this round has only the template, so the AI draft of an
    # earlier round (P-59)
    narrative_via: str | None = None
    narrative_round: int | None = None  # earlier than the latest round: carried over
    # carried over: the `narrative.drafted` data (draft, source, simulated) of the round
    # the text comes from, which the signature records as what was edited (QA, P-59)
    narrative_base: dict[str, Any] | None = None
    query_codes: list[str] = field(default_factory=list)
    query_text: str | None = None
    query_by: str | None = None
    query_at: str | None = None
    latest: str | None = None  # "narrative" or "query": the draft saved last

    @property
    def narrative_edited(self) -> bool:
        """The narrative is the Safety Specialist's own unsigned edit."""
        return self.narrative is not None and self.narrative_via != "earlier_ai"


def qc_drafts(store: FileCaseStore, case_id: str) -> QcDrafts:
    """The latest unsigned drafts for the latest QC round. A query draft belongs to its
    round. The narrative carries over (Nirav's review, 2026-11-19, P-59): after a Site
    Query the next round starts from the Safety Specialist's latest narrative edit; with
    no edit, and only the study template in this round (the AI could not draft, e.g. no
    recorded answer for the new documents), from the latest earlier AI draft.
    `narrative_round` then names the earlier round."""
    events = store.events(case_id)
    rounds = [e for e in events if e["type"] == "qc.checks_completed"]
    if not rounds:
        return QcDrafts()
    rnd, since = rounds[-1]["data"].get("round"), rounds[-1]["seq"]
    out: dict[str, Any] = {}
    drafted: list[tuple[dict[str, Any], int | None]] = []  # (narrative.drafted, its round)
    for e in events:
        data = e.get("data") or {}
        this_round = e["seq"] > since and data.get("round") == rnd
        if e["type"] == "narrative.drafted":
            drafted.append((e, None))
        elif e["type"] == "qc.checks_completed" and drafted and drafted[-1][1] is None:
            drafted[-1] = (drafted[-1][0], data.get("round"))
        elif e["type"] == "narrative.draft_edited":
            out |= {"narrative": store.get_file(case_id, data["draft"]["path"]).decode(),
                    "narrative_by": e["actor"], "narrative_at": e["at"],
                    "narrative_via": data.get("via"),
                    "narrative_round": data.get("round")}  # fmt: skip
            if this_round:
                out["latest"] = "narrative"
        elif e["type"] == "query.drafted" and this_round:
            out |= {"query_codes": list(data.get("codes") or []),
                    "query_text": data.get("text") or "", "query_by": e["actor"],
                    "query_at": e["at"], "latest": "query"}  # fmt: skip
    if "narrative" not in out and drafted and drafted[-1][0]["data"].get("source") == "template":
        earlier = [(e, r) for e, r in drafted[:-1] if e["data"].get("source") == "ai"]
        if earlier:
            e, r = earlier[-1]
            out |= {"narrative": store.get_file(case_id, e["data"]["draft"]["path"]).decode(),
                    "narrative_by": e["actor"], "narrative_at": e["at"],
                    "narrative_via": "earlier_ai", "narrative_round": r}  # fmt: skip
    r = out.get("narrative_round")
    if r is not None and r != rnd:
        out["narrative_base"] = next((e["data"] for e, n in drafted if n == r), None)
    return QcDrafts(**out)
