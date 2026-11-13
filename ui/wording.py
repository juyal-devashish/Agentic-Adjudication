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


ROLE_NAMES = {
    "site": "Site user",
    "safety_specialist": "Safety Specialist",
    "data_manager": "Data manager",
}


def doc_list(requested: list[str]) -> str:
    names = [r.replace("_", " ") for r in requested]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def next_step(
    role: str, state: State, *, site: str, requested: list[str], ready: bool, has_files: bool
) -> str:
    """One sentence: what the person looking at this case should do now."""
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
                "Check the redacted pages, then sign and submit."
                if ready
                else "Some files need attention before you can submit. See below."
            ),
            State.SUBMITTED: "Signed and submitted. Nothing more is needed from you.",
            State.QC_CHECKS: "Signed and submitted. Your documents are being checked.",
            State.QC_REVIEW: "Signed and submitted. Your documents are being checked.",
            State.QUERY_OPEN: "The quality check found a problem. Read the query below, then "
            "upload the missing or corrected documents.",
            State.NARRATIVE: "Your documents passed the quality check. Nothing more is needed.",
            State.NARRATIVE_REVIEW: "Your documents passed the quality check. Nothing more is "
            "needed.",
            State.READY_FOR_ADJUDICATION: "The case is ready for adjudication. Nothing more is "
            "needed from you.",
        }[state]
    return {
        State.EVENT_CREATED: "Related data is being pulled from the EDC.",
        State.AWAITING_DOCUMENTS: f"Waiting for site {site} to upload documents.",
        State.DEIDENTIFYING: f"Identifiers are being removed from site {site}'s uploads.",
        State.AWAITING_SITE_CONFIRMATION: f"Waiting for site {site} to review and sign.",
        State.SUBMITTED: "Signed by the site. The quality check starts automatically.",
        State.QC_CHECKS: "The quality check is running.",
        State.QC_REVIEW: (
            "The automatic quality check could not decide. Review it on the Quality check page."
            if role == "safety_specialist"
            else "Waiting for the Safety Specialist to review the quality check."
        ),
        State.QUERY_OPEN: f"Waiting for site {site} to answer the query.",
        State.NARRATIVE: "The narrative is being drafted.",
        State.NARRATIVE_REVIEW: (
            "Complete and sign the narrative on the Quality check page."
            if role == "safety_specialist"
            else "Waiting for the Safety Specialist to sign the narrative."
        ),
        State.READY_FOR_ADJUDICATION: "Ready for adjudication. No action is needed.",
    }[state]


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
