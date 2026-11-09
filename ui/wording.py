"""UI copy in one place, written for the person using the screen (docs/ui-design-plan.md,
"Copy rules"). Actions keep the same verb from button to confirmation."""

from __future__ import annotations

from adjud.workflow import State

IMPORT_OUTCOME = {
    "created": "New case",
    "not_eligible": "Not eligible",
    "changed": "Updated",
    "unchanged": "No change",
    "stale": "Older than the stored record; ignored",
}

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
        }[state]
    return {
        State.EVENT_CREATED: "Related data is being pulled from the EDC.",
        State.AWAITING_DOCUMENTS: f"Waiting for site {site} to upload documents.",
        State.DEIDENTIFYING: f"Identifiers are being removed from site {site}'s uploads.",
        State.AWAITING_SITE_CONFIRMATION: f"Waiting for site {site} to review and sign.",
        State.SUBMITTED: "Signed by the site. The package for adjudication is on the case record.",
    }[state]


def import_summary(rep: dict) -> str:
    if rep["status"] != "ACCEPTED":
        return (
            f"{rep['file']} was not imported: {len(rep['errors'])} problem(s) found. "
            "Nothing from this file was saved. Fix the rows below and import it again."
        )
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
