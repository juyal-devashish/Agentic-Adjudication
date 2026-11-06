"""Site review and submission (mvp-flow-1.md §7.10, F1-20 to F1-26).

    change_redactions()  untick findings (reason required) and/or add terms
                         -> redactions.changed (AWAITING_SITE_CONFIRMATION -> DEIDENTIFYING)
    delete_document()    -> document.deleted
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
from adjud.workflow import State

SUBMIT_MEANING = "Submitted — documents complete and de-identified"


class ReviewRefused(Exception):
    pass


def change_redactions(
    store: FileCaseStore,
    case_id: str,
    actor: str,
    *,
    reason: str | None = None,
    keep: list[dict[str, str]] | None = None,
    add_terms: list[str] | None = None,
) -> None:
    """`keep`: [{"file", "id", "masked", "category"}] findings to un-redact (F1-21).
    `add_terms`: extra terms to redact on all pages of all files (F1-22)."""
    keep = keep or []
    terms = [t.strip() for t in add_terms or [] if t and t.strip()]
    if not keep and not terms:
        raise ReviewRefused("nothing to change")
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
    store.append_event(
        case_id,
        EventDraft(type="document.deleted", actor=actor, data={"name": name}, reason=reason),
    )


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
        reasons.append(f"case is {state.value}, not AWAITING_SITE_CONFIRMATION")
    docs = documents_of(store, case_id)
    if not docs.files:
        reasons.append("no documents uploaded")
    records = []
    for name in sorted(docs.files):
        done = docs.processed.get(name)
        if done is None:
            reasons.append(f"{name}: not processed yet")
        elif done["status"] == "rejected":
            reasons.append(f"{name}: rejected ({done['reject_code']}); delete or replace it")
        elif done["status"] != "verified":
            reasons.append(f"{name}: verification found {done['residuals']} residual(s)")
        else:
            out = done["output"]
            records.append({"name": name, "path": out["path"], "sha256": out["sha256"]})
    return SubmissionStatus(ready=not reasons, reasons=reasons, records=records)


def submit(
    store: FileCaseStore, case_id: str, user_id: str, password: str, config_dir: Path
) -> dict[str, Any]:
    status = submission_status(store, case_id)
    if not status.ready:
        raise ReviewRefused("; ".join(status.reasons))
    user = load_users(config_dir).get(user_id)
    if user is None or user["role"] != "site":
        raise SignatureRefused("only a site user can submit")
    signature = sign(user, password, SUBMIT_MEANING, status.records)
    return store.append_event(
        case_id,
        EventDraft(
            type="submission.signed",
            actor=user_id,
            signature=signature,
            data={"files": status.records},
        ),
    )
