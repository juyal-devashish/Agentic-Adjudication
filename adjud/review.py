"""Site review and submission.

    change_redactions()  untick findings (reason required) and/or add terms
                         -> redactions.changed (AWAITING_SITE_CONFIRMATION -> DEIDENTIFYING)
    delete_document()    -> document.deleted
    relabel_document()   site corrects an automatic document type -> document.labelled
    submission_status()  can this case be signed now, and if not, why
    submit()             demo signature -> submission.signed (-> SUBMITTED)

No identifier is written to the event log: unticked findings are recorded by id and
masked text; added terms (which are identifiers) go to a quarantined case file that the
event references by hash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adjud.documents import documents_of
from adjud.events import EventDraft
from adjud.signatures import SignatureRefused, load_users, sign
from adjud.store import FileCaseStore
from adjud.workflow import State, replay

SUBMIT_MEANING = "Submitted — documents complete and de-identified"
_CHANGES_FILES = ("documents.uploaded", "document.deleted", "redactions.changed")


class ReviewRefused(Exception):
    pass


def _checked_box(b: dict[str, Any]) -> dict[str, Any]:
    """A box must name a file and a page and lie inside the page with a visible size."""
    try:
        x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in b["rect"])
        page, name = int(b["page"]), str(b["file"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewRefused("the box is not valid") from exc
    x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    if page < 1 or x1 - x0 < 0.005 or y1 - y0 < 0.002:
        raise ReviewRefused("the box is too small; drag across the text to cover")
    return {"file": name, "page": page, "rect": [round(v, 5) for v in (x0, y0, x1, y1)]}


def change_redactions(
    store: FileCaseStore,
    case_id: str,
    actor: str,
    *,
    reason: str | None = None,
    keep: list[dict[str, str]] | None = None,
    add_terms: list[str] | None = None,
    boxes: list[dict[str, Any]] | None = None,
) -> None:
    """`keep`: [{"file", "id", "masked", "category"}] findings to un-redact (F1-21).
    `add_terms`: extra terms to redact on all pages of all files (F1-22).
    `boxes`: [{"file", "page", "rect": [x0, y0, x1, y1]}] areas to black out, drawn on the
    redacted preview; rect as fractions of the page (plan P-30). Adding redaction needs no
    reason; only un-redacting does."""
    keep = keep or []
    terms = [t.strip() for t in add_terms or [] if t and t.strip()]
    boxes = [_checked_box(b) for b in boxes or []]
    if not keep and not terms and not boxes:
        raise ReviewRefused("nothing to change")
    unknown = {b["file"] for b in boxes} - set(documents_of(store, case_id).files)
    if unknown:
        raise ReviewRefused(f"no document named {', '.join(sorted(unknown))} in this case")
    if keep and not (reason or "").strip():
        raise ReviewRefused("a reason is required to un-redact a finding")
    data: dict[str, Any] = {"keep": {}, "kept": []}
    for k in keep:
        data["keep"].setdefault(k["file"], []).append(k["id"])
        data["kept"].append({key: k.get(key) for key in ("file", "id", "masked", "category")})
    if terms:
        n = len(list((store.case_dir(case_id) / "documents" / "site_terms").glob("*.txt"))) + 1
        rel = f"documents/site_terms/terms_v{n}.txt"
        sha = store.put_file(case_id, rel, ("\n".join(terms) + "\n").encode())
        data["terms"] = {"path": rel, "sha256": sha, "count": len(terms)}
    if boxes:
        data["boxes"] = boxes
    store.append_event(
        case_id,
        EventDraft(
            type="redactions.changed", actor=actor, data=data, reason=(reason or "").strip() or None
        ),
    )


def delete_document(
    store: FileCaseStore, case_id: str, name: str, actor: str, reason: str | None = None
) -> None:
    if name not in documents_of(store, case_id).files:
        raise ReviewRefused(f"no current document named {name!r}")
    if store.state(case_id).state == State.DEIDENTIFYING:  # e.g. a second tab's upload
        raise ReviewRefused("identifiers are being removed; try again when that is done")
    store.append_event(
        case_id,
        EventDraft(type="document.deleted", actor=actor, data={"name": name}, reason=reason),
    )


def relabel_document(
    store: FileCaseStore, case_id: str, name: str, label: str, actor: str, config_dir: Path
) -> None:
    """The site corrects a document's type during its review (P-38). The study's checklist
    lists the types; the state does not change."""
    from adjud.qc import Checklist

    docs = documents_of(store, case_id)
    if name not in docs.files:
        raise ReviewRefused(f"no current document named {name!r}")
    study = store.state(case_id).study
    if label not in Checklist.for_study(config_dir, study).types:
        raise ReviewRefused(f"{label!r} is not a document type of this study")
    previous, _ = docs.type_of(name)
    if label == previous:
        return
    store.append_event(
        case_id,
        EventDraft(type="document.labelled", actor=actor,
                   data={"name": name, "label": label, "previous": previous}),
    )  # fmt: skip


@dataclass
class SubmissionStatus:
    ready: bool
    reasons: list[str] = field(default_factory=list)
    records: list[dict[str, str]] = field(default_factory=list)  # redacted files to sign


def submission_status(store: FileCaseStore, case_id: str) -> SubmissionStatus:
    """F1-26: Sign and submit only when every current file has passed verification in
    the latest de-identification run."""
    reasons = []
    state = store.state(case_id).state
    if state != State.AWAITING_SITE_CONFIRMATION:
        reasons.append("the case is not waiting for site review")
    docs = documents_of(store, case_id)
    if not docs.files:
        reasons.append("no documents uploaded")
    records = []
    for name in sorted(docs.files):
        done = docs.processed.get(name)
        if done is None:
            reasons.append(f"{name} has not been processed yet")
        elif done["status"] == "rejected":
            reasons.append(f"{name} was rejected; remove it or upload a replacement")
        elif done["status"] != "verified":
            n = done["residuals"]
            reasons.append(f"{name} still contains {n} possible identifier{'s' if n != 1 else ''}")
        else:
            out = done["output"]
            records.append({"name": name, "path": out["path"], "sha256": out["sha256"]})
    return SubmissionStatus(ready=not reasons, reasons=reasons, records=records)


def submit(
    store: FileCaseStore, case_id: str, user_id: str, password: str, config_dir: Path
) -> dict[str, Any]:
    seen = len(store.events(case_id))  # read before the files to sign (B37)
    status = submission_status(store, case_id)
    if not status.ready:
        raise ReviewRefused("; ".join(status.reasons))
    user = load_users(config_dir).get(user_id)
    if user is None or user["role"] != "site":
        raise SignatureRefused("only a site user can submit")
    signature = sign(user, password, SUBMIT_MEANING, status.records)

    def still_waiting(prior: list[dict[str, Any]]) -> None:  # signed in another tab meanwhile
        if replay(prior).state != State.AWAITING_SITE_CONFIRMATION:
            raise ReviewRefused("the case is not waiting for site review")
        if any(e["type"] in _CHANGES_FILES or e["type"].startswith("deid.") for e in prior[seen:]):
            raise ReviewRefused("the documents changed while signing; review them and sign again")

    return store.append_event(
        case_id,
        EventDraft(
            type="submission.signed",
            actor=user_id,
            signature=signature,
            data={"files": status.records},
        ),
        expect=still_waiting,
    )
