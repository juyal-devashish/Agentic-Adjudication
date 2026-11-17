"""The AI Suggestion for the Quality Check and the Suggested Outcome (P-39, ADR 0001).

The `qc_suggestion` agent answers once per QC round. Code then checks each Finding on
its own: an EDC field's finding is used only when its status is allowed, its confidence
is at or above the study's threshold and every quote is found on the page it cites;
otherwise that finding is "not sure", with the reasons. The EDC value shown is always
the snapshot's, never the agent's. Required Documents come from the checklist (code);
the agent's "would help" documents are advice and never change the outcome.

`suggested_outcome` is code, not AI: query when a check failed or a usable Finding shows
a mismatch; pass when every check passed and the event term and onset Findings are
usable (doubt on the other fields is shown but does not hold a pass back); otherwise
none ("unclear"), with the reasons. The Safety Specialist decides and signs either way.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adjud.ai.functions import FIELD_STATUSES
from adjud.ai.gates import citation_found
from adjud.ai.gateway import AiResult

FIELDS = {
    "ae_term": "Event term",
    "onset_date": "Onset date",
    "end_date": "End date",
    "outcome": "Outcome",
    "serious": "Serious",
}
CORE_FIELDS = ("ae_term", "onset_date")  # must be sure for a suggested pass
PageText = Callable[[str, int], str | None]


def _edc_value(ae: dict[str, Any], field: str) -> str:
    value = ae.get(field)
    if field == "serious":
        return {"Y": "Yes", "N": "No"}.get(str(value), str(value or ""))
    return (
        str(value or "").replace("_", " ").capitalize() if field == "outcome" else str(value or "")
    )


def assess(
    result: AiResult,
    ae: dict[str, Any],
    *,
    page_text: PageText,
    min_confidence: float,
    allowed_types: list[str],
    present_types: set[str],
) -> dict[str, Any]:
    """The agent's answer, each Finding checked by code. `status` is ok when the agent
    answered with a valid output, else the gateway's status (unavailable, invalid, ...)."""
    out = result.output if result.ok and result.output else None
    if out is None:
        return {"status": result.status, "note": result.note, "label": result.label,
                "fields": [], "would_help": [], "summary": ""}  # fmt: skip
    by_field = {f.get("field"): f for f in out.get("fields") or []}
    fields = []
    for key, label in FIELDS.items():
        f = by_field.get(key)
        row = {"field": key, "label": label, "edc_value": _edc_value(ae, key)}
        if f is None:
            fields.append({**row, "document_value": "", "status": "not_sure",
                           "reason": "", "evidence": [],
                           "problems": ["the AI gave no answer for it"]})  # fmt: skip
            continue
        problems = []
        if f.get("status") not in FIELD_STATUSES:
            problems.append(f"status {f.get('status')!r} is not allowed")
        conf = float(f.get("confidence") or 0)
        if conf < min_confidence:
            problems.append(f"confidence {conf:.2f} is below {min_confidence:.2f}")
        evidence = f.get("evidence") or []
        if f.get("status") in ("match", "mismatch") and not evidence:
            problems.append("no quote given")
        for c in evidence:
            if not citation_found(str(c.get("quote", "")), page_text(c["file"], int(c["page"]))):
                problems.append(f"quote not found on {c['file']} page {c['page']}")
        fields.append({
            **row, "document_value": f.get("document_value", ""),
            "status": "not_sure" if problems else f["status"], "said": f.get("status"),
            "confidence": conf, "reason": f.get("reason", ""), "evidence": evidence,
            "problems": problems,
        })  # fmt: skip
    would_help = [
        {"type": d["document_type"], "reason": d.get("reason", "")}
        for d in out.get("would_help") or []
        if d.get("document_type") in allowed_types
        and d["document_type"] not in present_types
        and d["document_type"] != "other"
    ]
    return {"status": "ok", "note": result.note, "label": result.label, "fields": fields,
            "would_help": would_help, "summary": out.get("summary", "")}  # fmt: skip


def event_from_fields(suggestion: dict[str, Any]) -> dict[str, str] | None:
    """What the usable Findings say about the event itself (event term and onset), for the
    `subject_and_onset` check when code could not find the subject ID or the onset."""
    rows = {f["field"]: f for f in suggestion.get("fields") or []}
    core = [rows.get("ae_term"), rows.get("onset_date")]
    if any(r and r["status"] == "mismatch" for r in core):
        bad = [r for r in core if r and r["status"] == "mismatch"]
        return {"verdict": "different_event", "reason": " ".join(r["reason"] for r in bad)}
    if all(r and r["status"] == "match" for r in core):
        return {"verdict": "matches", "reason": " ".join(r["reason"] for r in core if r)}
    return None


def suggested_outcome(
    checks: list[dict[str, Any]], suggestion: dict[str, Any]
) -> tuple[str, list[str], list[str]]:
    """(outcome, reasons, query codes): outcome is pass, query or unclear."""
    failed = [c for c in checks if c["status"] == "failed"]
    mismatched = [f for f in suggestion.get("fields") or [] if f["status"] == "mismatch"]
    if failed or mismatched:
        reasons = [c["detail"] for c in failed]
        reasons += [f"{f['label']}: EDC {f['edc_value'] or 'empty'}, documents "
                    f"{f['document_value'] or 'empty'}" for f in mismatched]  # fmt: skip
        codes = [c["query_code"] for c in failed if c.get("query_code")]
        if mismatched:
            codes.append("wrong_event")
        return "query", reasons, list(dict.fromkeys(codes))
    reasons = [c["detail"] or c["label"] for c in checks if c["status"] == "unknown"]
    if suggestion.get("status") != "ok":
        reasons.append(f"No AI Suggestion ({suggestion.get('note') or 'AI is off'})")
    # Only the event itself must be sure for a pass; doubt about the end date, outcome or
    # seriousness is shown with its reason but does not hold the pass back (P-39)
    unsure = [f for f in suggestion.get("fields") or []
              if f["status"] == "not_sure" and f["field"] in CORE_FIELDS]  # fmt: skip
    reasons += [f"{f['label']}: not sure ({'; '.join(f['problems'])})" for f in unsure]
    return ("unclear" if reasons else "pass"), reasons, []
