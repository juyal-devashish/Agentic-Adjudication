"""UI copy in one place, written for the person using the screen (docs/ui-design-plan.md,
"Copy rules"). Actions keep the same verb from button to confirmation."""

from __future__ import annotations

from adjud.intake import SEMICOLON_HINT
from adjud.workflow import State

IMPORT_OUTCOME = {
    "created": "New case",
    "not_eligible": "Not eligible",
    "changed": "Updated",
    "unchanged": "No change",
    "stale": "Older than the stored record; ignored",
}

# The manual AE form: one readable label per EDC column, in the order a person fills it in.
AE_FIELD_LABELS = {
    "subject_id": "Subject ID",
    "site_id": "Site",
    "ae_id": "AE ID",
    "ae_term": "AE term, as reported",
    "onset_date": "Onset date",
    "end_date": "End date",
    "serious": "Serious",
    "seriousness_criteria": "Seriousness criterion",
    "outcome": "Outcome",
    "severity": "Severity",
    "relatedness_investigator": "Related to study drug (investigator)",
    "pt_code": "Preferred term code",
    "llt_code": "Lowest level term code",
    "soc_code": "System organ class code",
    "meddra_version": "MedDRA version",
    "last_modified": "Last changed in the EDC",
    "study_id": "Study",
    "reason": "Why it is entered by hand",
}
# Coded columns get a picker; the values are the EDC's codes.
AE_CHOICES = {
    "serious": ["Y", "N"],
    "seriousness_criteria": [
        "DEATH",
        "LIFE_THREATENING",
        "HOSPITALISATION",
        "DISABILITY",
        "CONGENITAL_ANOMALY",
        "OTHER_MEDICALLY_IMPORTANT",
    ],  # fmt: skip
    "outcome": [
        "RECOVERED",
        "RECOVERING",
        "NOT_RECOVERED",
        "RECOVERED_WITH_SEQUELAE",
        "FATAL",
        "UNKNOWN",
    ],  # fmt: skip
    "severity": ["MILD", "MODERATE", "SEVERE"],
    "relatedness_investigator": ["NOT_RELATED", "RELATED"],
}
ENTRY_PROBLEMS = {
    "MISSING_VALUE": "required",
    "MISSING_REASON": "required",
    "INVALID_DATE": "use a date",
    "END_BEFORE_ONSET": "before the onset date",
    "UNKNOWN_SUBJECT": "not a subject of this study in the EDC",
    "SITE_MISMATCH": "not this subject's site",
    "WRONG_STUDY": "not this study",
    "INVALID_VALUE": "not a valid value",
    "INVALID_DATETIME": "use a date and time, e.g. 2026-08-21T09:00:00Z",
}


def code_words(code: str) -> str:
    """'HOSPITALISATION' -> 'Hospitalisation', 'Y' -> 'Yes'."""
    return {"Y": "Yes", "N": "No"}.get(code, code.replace("_", " ").capitalize())


ROLE_NAMES = {  # in demo order; the first is where the app opens (on AE import)
    "data_manager": "Data manager",
    "site": "Site user",
    "safety_specialist": "Safety Specialist",
    "cec_coordinator": "CEC Coordinator",  # Flows 3 and 4 (P-41)
    "adjudicator": "Adjudicator",
}


# Every page of the app: key (the URL path) -> file, menu title and icon. app.py registers
# them all; a view opens one by its file (st.switch_page).
PAGES = {
    "import": ("views/edc_input.py", "AE import", ":material/move_to_inbox:"),
    "documents": ("views/site_documents.py", "Documents", ":material/description:"),
    "qc": ("views/quality_check.py", "Quality check", ":material/rule:"),
    "assignments": ("views/assignments.py", "Assignments", ":material/assignment_ind:"),
    "adjudicate": ("views/adjudication.py", "Adjudication", ":material/gavel:"),
    "committee": ("views/committee.py", "Committee", ":material/groups:"),
    "record": ("views/results.py", "Case record", ":material/fact_check:"),
}

# The pages each role works on, its landing page first. Case record is read-only for all.
# Each role lands on the page of its own work, with its tasks listed at the top; work
# starts in Slack, with the Agent (P-56, P-58; ADR 0003).
ROLE_PAGES = {
    "data_manager": ["import", "record"],
    "site": ["documents", "record"],
    "safety_specialist": ["qc", "record"],
    "cec_coordinator": ["assignments", "committee", "record"],
    "adjudicator": ["adjudicate", "committee", "record"],
}


def in_sentence(name: str) -> str:
    """'Laboratory results' -> 'laboratory results'; 'ECG report' stays."""
    name = name.replace("_", " ")
    return name if name[1:2].isupper() else name[:1].lower() + name[1:]


def doc_list(requested: list[str]) -> str:
    names = [in_sentence(r) for r in requested]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def next_step(
    role: str,
    state: State,
    *,
    site: str,
    requested: list[str],
    ready: bool,
    has_files: bool,
    review: str | None = None,
) -> str:
    """One sentence: what the person looking at this case should do now. `review` is an
    adjudicator's own review in the current pass: "open", "signed" or None (they hold none)."""
    if state == State.WITHDRAWN:
        return "This case was withdrawn. No action is needed."
    if role == "site":
        return {
            State.EVENT_CREATED: "The case is being set up. Documents can be uploaded shortly.",
            State.AWAITING_DOCUMENTS: (
                f"Upload the requested documents: {doc_list(requested)}."
                if not has_files
                else "Some files could not be processed. Replace them, or upload the rest."
            ),
            State.DEIDENTIFYING: "Identifiers are being removed. Scanned pages take a few "
            "seconds each.",
            State.AWAITING_SITE_CONFIRMATION: (
                f"Upload the requested documents: {doc_list(requested)}."
                if not has_files  # the site removed every file (QA, 2026-11-19)
                else "Check the redacted pages, then sign and submit."
                if ready
                else "Some files need attention before you can submit. See below."
            ),
            State.SUBMITTED: "Signed and submitted. Nothing more is needed from you.",
            State.QC_CHECKS: "Signed and submitted. Your documents are being checked.",
            State.QC_REVIEW: "Signed and submitted. Your documents are being checked.",
            State.QUERY_OPEN: "A query is open. Read it below, then upload the missing or "
            "corrected documents.",
            State.NARRATIVE: "Your documents passed the quality check. Nothing more is needed.",
            State.NARRATIVE_REVIEW: "Your documents passed the quality check. Nothing more is "
            "needed.",
            State.READY_FOR_ADJUDICATION: "The case is ready for adjudication. Nothing more is "
            "needed from you.",
        }.get(
            state, "The case is with adjudication. Nothing more is needed from you."
        )  # Flows 3–4: no adjudication details for the site (ACC-04)
    return {
        State.EVENT_CREATED: "Related data is being pulled from the EDC.",
        State.AWAITING_DOCUMENTS: f"Waiting for {site} to upload documents.",
        State.DEIDENTIFYING: f"Identifiers are being removed from {site}'s uploads.",
        State.AWAITING_SITE_CONFIRMATION: f"Waiting for {site} to review and sign.",
        State.SUBMITTED: "Signed by the site. The quality check starts automatically.",
        State.QC_CHECKS: "The quality check is running.",
        State.QC_REVIEW: (
            "The checks, the AI Suggestion and the narrative draft are ready. Review them, "
            "then sign the pass with the narrative, or send a query."
            if role == "safety_specialist"
            else "Waiting for the Safety Specialist to review the quality check."
        ),
        State.QUERY_OPEN: f"Waiting for {site} to answer the query.",
        State.NARRATIVE: "The narrative is being drafted.",
        State.NARRATIVE_REVIEW: (
            "The narrative draft is ready. Check it against the dossier, then sign it."
            if role == "safety_specialist"
            else "Waiting for the Safety Specialist to sign the narrative."
        ),
        State.READY_FOR_ADJUDICATION: "Ready for adjudication. The reviews are assigned "
        "automatically.",
        State.ADJ_REVIEW: (
            "Two adjudicators are reviewing independently."
            if role != "adjudicator"
            else "Your independent review is open: complete and sign the form."
            if review == "open"
            else "You signed your review. Waiting for the other review."
            if review == "signed"
            else "You hold no review on this case."
        ),
        State.ADJ3_REVIEW: (
            "The reviews disagree on some fields: as Adjudicator 3, review them and sign the form."
            if role == "adjudicator" and review == "open"
            else "The reviews disagree on some fields; Adjudicator 3 is reviewing them."
        ),
        State.CEC_MEETING: (
            "Record the committee's meeting and its decision on each open field."
            if role == "cec_coordinator"
            else "Waiting for the committee: the Chair signs the recorded consensus."
        ),
        State.MORE_DATA: "More data was requested; the site gets a query.",
        State.OUTCOME: "The outcome is being derived from the signed answers.",
        State.EXPORT: "The outcome is being exported to the EDC.",
        State.CLOSED: (
            "Closed. A re-adjudication can be started on the Assignments page."
            if role == "cec_coordinator"
            else "Closed. No action is needed."
        ),
    }[state]


DOCUMENTS_PAGE, QC_PAGE = "views/site_documents.py", "views/quality_check.py"
ADJ_PAGE, COMMITTEE_PAGE = "views/adjudication.py", "views/committee.py"
ASSIGN_PAGE = "views/assignments.py"


def next_page(role: str, state: State, *, review: str | None = None) -> tuple[str, str, str] | None:
    """Where the person looking at this case acts next: (page, link text, icon), or None
    when there is nothing for them to do. Matches next_step(): only states in which this
    role has an action of its own get a link; an adjudicator's form only while their review
    is open."""
    if role == "site":
        return {
            State.AWAITING_DOCUMENTS: (DOCUMENTS_PAGE, "Upload documents", ":material/upload:"),
            State.AWAITING_SITE_CONFIRMATION: (
                DOCUMENTS_PAGE,
                "Review and sign the documents",
                ":material/description:",
            ),
            State.QUERY_OPEN: (DOCUMENTS_PAGE, "Answer the query", ":material/upload:"),
        }.get(state)
    if role == "safety_specialist":
        return {
            State.QC_REVIEW: (QC_PAGE, "Review the quality check", ":material/rule:"),
            State.NARRATIVE_REVIEW: (QC_PAGE, "Complete the narrative", ":material/rule:"),
        }.get(state)
    if role == "adjudicator":
        if state in (State.ADJ_REVIEW, State.ADJ3_REVIEW):
            return (ADJ_PAGE, "Open the adjudication form", ":material/gavel:") if (
                review == "open") else None  # fmt: skip
        return {
            State.CEC_MEETING: (COMMITTEE_PAGE, "Committee", ":material/groups:"),
        }.get(state)
    if role == "cec_coordinator":
        return {
            State.ADJ_REVIEW: (ASSIGN_PAGE, "Assignments", ":material/assignment_ind:"),
            State.ADJ3_REVIEW: (ASSIGN_PAGE, "Assignments", ":material/assignment_ind:"),
            State.CEC_MEETING: (COMMITTEE_PAGE, "Record the meeting", ":material/groups:"),
            State.CLOSED: (ASSIGN_PAGE, "Re-adjudicate", ":material/replay:"),
        }.get(state)
    return None


def import_summary(rep: dict) -> str:
    if rep["status"] != "ACCEPTED":
        head = f"{rep['file']} was not imported. Nothing from this file was saved."
        if any(e.get("message") == SEMICOLON_HINT for e in rep["errors"]):
            return f"{head} It uses semicolons: save it as comma-separated CSV and import it again."
        where = "columns" if all(e["code"] == "MISSING_COLUMN" for e in rep["errors"]) else "rows"
        n = len(rep["errors"])
        found = f"{n} problem{'s' if n != 1 else ''} found"
        return f"{head} {found}: fix the {where} below and import it again."
    parts = [f"{rep['records']} record{'s' if rep['records'] != 1 else ''}"]
    details = [
        (rep["new_cases"], "new case", "new cases"),
        (rep["not_eligible"], "not eligible", "not eligible"),
        (rep["changed"], "updated", "updated"),
        (rep["unchanged"], "unchanged", "unchanged"),
        (rep["stale"], "ignored as older", "ignored as older"),
    ]
    counted = [f"{n} {one if n == 1 else many}" for n, one, many in details if n]
    return f"Imported {rep['file']}: {parts[0]}" + (f", {', '.join(counted)}." if counted else ".")
