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

import yaml

from adjud.ai.functions import DOCUMENT_TYPE, EVENT_MATCH, EVENT_VERDICTS, NARRATIVE
from adjud.ai.gates import GateResult, gate
from adjud.ai.gateway import AiResult, Gateway
from adjud.ai.tools import case_toolbox, dossier_page_text, output_pages, page_text
from adjud.dossier import build_and_record, latest_dossier
from adjud.events import EventDraft
from adjud.handoff import latest_manifest_ref
from adjud.notify import Outbox
from adjud.qc import Checklist, run_checks
from adjud.signatures import SignatureRefused, load_users, sign
from adjud.store import FileCaseStore, json_bytes
from adjud.workflow import State

SYSTEM_QC = "system:qc"
AGENT_QC = "agent:qc"  # a QC decision that used an agent answer which passed its gate
AGENT_NARRATIVE = "agent:narrative"  # a narrative approved because its gate passed
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
        """One QC round. Deterministic checks first (step 14):
        - a check failed for a clear reason → a query goes to the site automatically
        - every check passed                 → QC passes, no person involved (P-36)
        - code cannot tell                   → the QC agent (step 15), else a person
        """
        n = _rounds(self.store, case_id, "qc.checks_completed") + 1
        manifest_ref = _latest(self.store, case_id, "qc.started")["data"]["manifest"]
        manifest = self.store.get_json(case_id, manifest_ref["path"])
        outcome = run_checks(self.store, case_id, manifest, self.checklist)
        ai: list[dict[str, Any]] = []
        actor = SYSTEM_QC
        if outcome.unknown and not outcome.failed:  # step 15: agents fill in the unknowns
            ai_types, event = self._qc_agents(case_id, manifest, outcome, ai)
            if ai_types or event:
                outcome = run_checks(self.store, case_id, manifest, self.checklist,
                                     ai_types=ai_types, event=event)  # fmt: skip
                actor = AGENT_QC
        if outcome.failed:
            decision, reasons = "query", [c.detail for c in outcome.failed]
        elif not outcome.unknown:
            decision, reasons = "pass", []
        else:
            decision = "review"
            reasons = [c.detail or c.label for c in outcome.unknown]
            if not any(a["status"] != "unavailable" for a in ai):
                reasons.append("AI is off: the Safety Specialist decides what code could not")
            reasons += [f"{a['task']} for {a['about']}: {r}" for a in ai
                        for r in (a["gate"] or {}).get("reasons", [])]  # fmt: skip
        result = {
            "round": n,
            "manifest": manifest_ref,
            "checks": outcome.as_dicts(),
            "types": outcome.types,
            "decision": decision,
            "reasons": reasons,
            "ai": ai,
        }
        rel = f"qc/checks_v{n}.json"
        sha = self.store.put_file(case_id, rel, json_bytes(result))
        self._event(case_id, "qc.checks_completed", SYSTEM_QC,
                    {"round": n, "results": {"path": rel, "sha256": sha},
                     "decision": decision})  # fmt: skip
        if decision == "query":
            codes = list(dict.fromkeys(c.query_code for c in outcome.failed if c.query_code))
            self.open_query(case_id, codes, " ".join(f"{r}." for r in reasons), actor=actor)
        elif decision == "pass":
            by = "checks and AI" if actor == AGENT_QC else "checks"
            self._event(case_id, "qc.passed", actor,
                        {"results": {"path": rel, "sha256": sha}, "by": by})  # fmt: skip
        else:
            self._event(case_id, "qc.review_needed", SYSTEM_QC, {"reasons": reasons})
            self._notify(case_id, "role:safety_specialist", "qc_review_needed", SYSTEM_QC)
        return result

    # -- QC agents (step 15) -----------------------------------------------------------------

    def _qc_agents(
        self,
        case_id: str,
        manifest: dict[str, Any],
        outcome: Any,
        log: list[dict[str, Any]],
    ) -> tuple[dict[str, str], dict[str, str] | None]:
        """Ask the agents about what code left unknown; keep only gated answers."""
        assert self.gateway is not None
        cfg, toolbox = self.gateway.cfg, case_toolbox(self.store, case_id)
        pages_of = {d["source_name"]: len(output_pages(self.store, case_id, d["path"]))
                    for d in manifest["documents"]}  # fmt: skip

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

        event = None
        check = next(c for c in outcome.checks if c.id == "subject_and_onset")
        if check.status == "unknown":
            ae = self.store.get_json(case_id, manifest["ae_snapshot"]["path"])
            payload = {
                "ae": {k: ae.get(k) for k in ("ae_term", "onset_date", "end_date", "outcome")},
                "documents": [{"file": n, "pages": c} for n, c in sorted(pages_of.items())],
            }
            r = self.gateway.run(case_id, EVENT_MATCH, payload, toolbox)
            out = r.output or {}
            g = None
            if r.status != "unavailable":
                verdict = out.get("verdict")
                g = gate(r, min_confidence=cfg.min_confidence, page_text=text_of,
                         citations=out.get("evidence") or [],
                         checks=[(f"verdict {verdict!r} is not allowed", verdict in EVENT_VERDICTS),
                                 ("the AI could not tell", verdict != "unclear"),
                                 ("no quote given", bool(out.get("evidence")))])  # fmt: skip
                if g.passed:
                    event = {"verdict": out["verdict"], "reason": out["reason"]}
            answer = f"{out.get('verdict', '').replace('_', ' ')}: {out.get('reason', '')}"
            self._log_ai(case_id, log, "Event match", "all documents", r, g,
                         answer if out else "")  # fmt: skip
        return ai_types, event

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

    def run_narrative(self, case_id: str) -> str:
        """Build the dossier, then draft the narrative (step 17). The narrative agent writes
        facts-only statements, each quoting a dossier page; if its gate passes the
        narrative is approved and frozen with no person involved. Otherwise, or with AI
        off, the draft (the agent's, labelled, or the study template) goes to the Safety
        Specialist."""
        dossier = self.ensure_dossier(case_id)
        n = _rounds(self.store, case_id, "narrative.drafted") + 1
        r, g, statements = self._narrative_agent(case_id, dossier)
        if statements:
            text = "\n".join(f"{s['text']} [dossier p. {s['page']}]" for s in statements)
            source = "ai"
        else:
            text, source = self.template_draft(case_id), "template"
        rel = f"narrative/draft_v{n}.md"
        sha = self.store.put_file(case_id, rel, text.encode())
        self._event(case_id, "narrative.drafted", SYSTEM_NARRATIVE,
                    {"draft": {"path": rel, "sha256": sha}, "source": source,
                     "simulated": r.simulated, "call": r.call})  # fmt: skip
        if g is not None and g.passed:
            k = (
                _rounds(self.store, case_id, "narrative.signed")
                + _rounds(self.store, case_id, "narrative.approved")
                + 1
            )
            final = f"narrative/narrative_v{k}.md"
            fsha = self.store.put_file(case_id, final, text.encode() + b"\n")
            self._event(case_id, "narrative.approved", AGENT_NARRATIVE,
                        {"narrative": {"path": final, "sha256": fsha}, "source": "ai",
                         "simulated": r.simulated, "call": r.call})  # fmt: skip
            return text
        if r.status == "unavailable":
            reasons = ["AI is off: the Safety Specialist completes the narrative"]
        else:
            reasons = [f"AI draft not accepted: {x}" for x in (g.reasons if g else [r.note])]
        self._event(case_id, "narrative.review_needed", SYSTEM_NARRATIVE, {"reasons": reasons})
        self._notify(case_id, "role:safety_specialist", "narrative_review_needed",
                     SYSTEM_NARRATIVE)  # fmt: skip
        return text

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
                   user_id: str | None = None, password: str | None = None,
                   actor: str = SYSTEM_QC) -> None:  # fmt: skip
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
            self._event(case_id, "query.opened", actor, data)
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
