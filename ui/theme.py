"""Visual system pieces the Streamlit theme cannot express (docs/ui-design-plan.md):
the synthetic-data band, the case lifecycle rail, status words, the case heading, and
small typographic rules. Black is reserved for redaction and "where the case is now".
"""

from __future__ import annotations

import html

import streamlit as st

from adjud.workflow import State

INK, MUTED, VIOLET = "#1C2833", "#5A6B78", "#4A3AA0"
STATUS_INK = {"good": "#23704F", "bad": "#A6342B", "wait": "#86651A", "none": MUTED}

CSS = """
<style>
h1, h2, h3 { letter-spacing: -0.012em; }
h1 { line-height: 1.12; }
/* Running text stays readable: about 72 characters per line. */
[data-testid="stMarkdownContainer"] p, [data-testid="stCaptionContainer"] p { max-width: 72ch; }
[data-testid="stMainBlockContainer"] { max-width: 1180px; }
/* Figures line up in tables, dates and counts. */
body, [data-testid="stDataFrame"] { font-variant-numeric: tabular-nums; }
/* Visible keyboard focus everywhere, in stamp violet. */
:focus-visible { outline: 2px solid #4A3AA0 !important; outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}

.adj-band { background: #000; color: #fff; padding: .55rem .75rem; border-radius: 2px;
  font-size: .875rem; line-height: 1.35; }
.adj-band strong { font-weight: 700; }

.adj-case-id { font-size: .875rem; color: #5A6B78; margin: 0 0 .15rem 0; }
.adj-case-title { font-size: 2.25rem; font-weight: 700; letter-spacing: -0.015em;
  line-height: 1.12; margin: 0 0 .35rem 0; color: #1C2833; }
.adj-case-meta { font-size: 1rem; color: #3A4A57; margin: 0; max-width: 72ch; }

.adj-rail { display: flex; flex-wrap: wrap; gap: .25rem .4rem; list-style: none;
  padding: 0 !important; margin: 1.4rem 0 .9rem 0 !important; counter-reset: step; }
.adj-rail li { margin: 0 !important; }
.adj-rail li { counter-increment: step; font-size: .875rem; padding: .35rem .6rem;
  color: #5A6B78; border-bottom: 2px solid #C9D3D9; }
.adj-rail li::before { content: counter(step) "  "; font-weight: 700; }
.adj-rail li.done { color: #1C2833; border-bottom-color: #1C2833; }
.adj-rail li.now { background: #000; color: #fff; border-bottom-color: #000; font-weight: 600; }
.adj-rail li.off { text-decoration: line-through; }

.adj-next { font-size: 1.05rem; margin: 0 0 1.2rem 0; padding-left: .75rem;
  border-left: 3px solid #4A3AA0; max-width: 72ch; }

.adj-status { font-weight: 600; }
.adj-quiet { color: #5A6B78; font-size: .875rem; }
.adj-bar { display: inline-block; background: #000; color: #000; border-radius: 1px;
  padding: 0 .25rem; user-select: none; }
</style>
"""

# The Flow 1 lifecycle, in the words of the people using it. It is a real sequence, so
# it is numbered.
LIFECYCLE = [
    (State.EVENT_CREATED, "Case created"),
    (State.AWAITING_DOCUMENTS, "Documents requested"),
    (State.DEIDENTIFYING, "Removing identifiers"),
    (State.AWAITING_SITE_CONFIRMATION, "Site review"),
    (State.SUBMITTED, "Submitted"),
    (State.QC_CHECKS, "Quality check"),
    (State.NARRATIVE, "Narrative"),
    (State.READY_FOR_ADJUDICATION, "Ready for adjudication"),
]
# States that sit on another state's step of the rail.
RAIL_STEP = {
    State.QC_REVIEW: State.QC_CHECKS,
    State.QUERY_OPEN: State.QC_CHECKS,
    State.NARRATIVE_REVIEW: State.NARRATIVE,
}
STATE_WORDS = {
    State.EVENT_CREATED: "Case created",
    State.AWAITING_DOCUMENTS: "Waiting for documents",
    State.DEIDENTIFYING: "Removing identifiers",
    State.AWAITING_SITE_CONFIRMATION: "Waiting for site review",
    State.SUBMITTED: "Submitted",
    State.QC_CHECKS: "Quality check running",
    State.QC_REVIEW: "Waiting for QC review",
    State.QUERY_OPEN: "Query open with the site",
    State.NARRATIVE: "Narrative being drafted",
    State.NARRATIVE_REVIEW: "Waiting for narrative review",
    State.READY_FOR_ADJUDICATION: "Ready for adjudication",
    State.WITHDRAWN: "Withdrawn",
}
FLAG_WORDS = {
    "source_changed": "AE changed in the EDC",
    "no_longer_eligible": "No longer meets the rules",
    "possible_duplicate": "Possible duplicate",
}
CATEGORY_WORDS = {
    "PATIENT_NAME": "Patient name",
    "NEXT_OF_KIN": "Next of kin",
    "DOB": "Date of birth",
    "MRN": "Hospital number",
    "INSURANCE_NO": "Insurance number",
    "ADDRESS": "Address",
    "PHONE": "Phone number",
    "EMAIL": "Email address",
    "CASE_REFERENCE": "Case reference",
    "STAFF_NAME": "Staff name",
    "FACILITY": "Hospital or practice",
    "PERSON": "Person's name",
    "LOCATION": "Place",
    "SITE_TERM": "Term added by the site",
    "SEEDED": "Planted test value",
}
AE_FIELDS = [  # (field, label) in the order a clinician reads an AE
    ("ae_term", "Event term"),
    ("onset_date", "Onset"),
    ("end_date", "End"),
    ("serious", "Serious"),
    ("seriousness_criteria", "Seriousness"),
    ("outcome", "Outcome"),
    ("severity", "Severity"),
    ("relatedness_investigator", "Related to study drug"),
    ("pt_code", "Preferred term code"),
    ("soc_code", "Organ class code"),
    ("meddra_version", "Dictionary version"),
    ("subject_id", "Subject"),
    ("site_id", "Site"),
    ("ae_id", "AE number"),
    ("last_modified", "Last changed in the EDC"),
]


def category_words(category: str) -> str:
    return CATEGORY_WORDS.get(category, category.replace("_", " ").capitalize())


def plain_value(value: str) -> str:
    """Coded EDC values in words: 'NOT_RELATED' -> 'Not related', 'Y' -> 'Yes',
    '2026-03-14' -> '14 Mar 2026'."""
    import re

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        return date_words(value)
    if value in ("Y", "N"):
        return {"Y": "Yes", "N": "No"}[value]
    if value and value.isupper() and len(value) > 3:
        return value.replace("_", " ").capitalize()
    return value or "Not recorded"


def rules_html(results: list[dict]) -> str:
    """Eligibility explanation as a nested list of met / not met rules."""
    out = []
    for r in results:
        word = status("Met", "good") if r["passed"] else status("Not met", "bad")
        label = html.escape(f"{r.get('id') or ''} {r.get('description') or ''}")
        if "group" in r:
            kind = "all of" if r["group"] == "all" else "any of"
            out.append(f"<li>{word}: {label} ({kind})<ul>{rules_html(r['children'])}</ul></li>")
        else:
            actual = html.escape(str(r["actual"]) or "empty")
            why = "" if r["passed"] else f", {html.escape(r['reason'])}"
            out.append(
                f"<li>{word}: {label} <span class='adj-quiet'>"
                f"({html.escape(r['field'])} is {actual}{why})</span></li>"
            )
    return "".join(out)


FILE_STATUS = {  # word, ink
    "verified": ("Verified", "good"),
    "blocked": ("Needs attention", "bad"),
    "rejected": ("Rejected", "bad"),
    "processing": ("Processing", "wait"),
    "pending": ("Not processed", "wait"),
}


def apply() -> None:
    st.html(CSS)


def synthetic_band() -> None:
    st.html(
        '<div class="adj-band" role="note"><strong>Synthetic data only.</strong> '
        "For demonstration; not for real patient data and not validated.</div>"
    )


SHORT_STATE = {s: label.lower() for s, label in LIFECYCLE} | {
    State.QC_REVIEW: "QC review",
    State.QUERY_OPEN: "query open",
    State.NARRATIVE_REVIEW: "narrative review",
    State.WITHDRAWN: "withdrawn",
}


def short_state(state: State) -> str:
    """Compact form for the case picker: 'site review', 'submitted'."""
    return SHORT_STATE.get(state, str(state))


def state_words(state: State) -> str:
    return STATE_WORDS.get(state, str(state))


def flag_words(flags: list[str]) -> str:
    return ", ".join(FLAG_WORDS.get(f, f.replace("_", " ")) for f in flags)


def case_heading(case_id: str, title: str, meta: str) -> None:
    st.html(
        f'<p class="adj-case-id">Case {html.escape(case_id)}</p>'
        f'<h1 class="adj-case-title">{html.escape(title)}</h1>'
        f'<p class="adj-case-meta">{html.escape(meta)}</p>'
    )


def lifecycle(state: State) -> None:
    """The rail: where the case has been, and (in black) where it is now."""
    order = [s for s, _ in LIFECYCLE]
    step = RAIL_STEP.get(state, state)
    now = order.index(step) if step in order else -1
    items = []
    for i, (_, label) in enumerate(LIFECYCLE):
        cls = "now" if i == now else "done" if i < now else ""
        if state == State.WITHDRAWN:
            cls = "off"
        current = ' aria-current="step"' if i == now else ""
        items.append(f'<li class="{cls}"{current}>{html.escape(label)}</li>')
    note = ' <span class="adj-quiet">Withdrawn</span>' if state == State.WITHDRAWN else ""
    st.html(f'<ol class="adj-rail" aria-label="Case progress">{"".join(items)}</ol>{note}')


def next_step(text: str) -> None:
    st.html(f'<p class="adj-next">{html.escape(text)}</p>')


def status(word: str, ink: str) -> str:
    """Inline status word for HTML blocks."""
    return f'<span class="adj-status" style="color:{STATUS_INK[ink]}">{html.escape(word)}</span>'


def quiet(text: str) -> None:
    st.html(f'<p class="adj-quiet">{html.escape(text)}</p>')


def date_words(iso: str) -> str:
    """2026-03-14 -> 14 Mar 2026 (unambiguous for UK, German and US readers)."""
    from datetime import date

    try:
        return date.fromisoformat(iso[:10]).strftime("%-d %b %Y")
    except ValueError:
        return iso
