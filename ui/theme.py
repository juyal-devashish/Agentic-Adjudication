"""Visual system pieces the Streamlit theme cannot express (docs/ui-design-plan.md):
the synthetic-data band, the case lifecycle rail, status words, the case heading, and
small typographic rules. Black is reserved for redaction and "where the case is now".
"""

from __future__ import annotations

import html

import streamlit as st

from adjud import workflow
from adjud.workflow import State

INK, MUTED, VIOLET = "#1C2833", "#5A6B78", "#4A3AA0"
STATUS_INK = {"good": "#23704F", "bad": "#A6342B", "wait": "#86651A", "none": MUTED}

CSS = """
<style>
h1, h2, h3 { letter-spacing: -0.012em; }
h1 { line-height: 1.12; }
/* Running text stays readable: about 72 characters per line. */
[data-testid="stMarkdownContainer"] p, [data-testid="stCaptionContainer"] p { max-width: 72ch; }
/* The desk (form paper) and the case sheet on it: one surface for the work, so tables,
   inputs and sections read as parts of one page instead of floating on grey. */
[data-testid="stMain"], [data-testid="stHeader"] { background: #EDF1F3; }
[data-testid="stMainBlockContainer"] { max-width: 1180px; width: calc(100% - 4rem);
  background: #FAFBFB; border: 1px solid #D5DDE2; border-radius: 4px;
  padding: 1.5rem 2.25rem 3rem 2.25rem; margin: 3.25rem 2rem 2rem 2rem; }
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { width: 100%; padding: 1rem 1rem 2rem 1rem;
    margin: 3rem 0 0 0; border-left: 0; border-right: 0; border-radius: 0; }
}
/* Sections of a page are separated by a hairline, not by boxes. */
[data-testid="stMain"] [data-testid="stHeading"] h2 { border-top: 1px solid #D5DDE2;
  padding-top: 1.1rem; margin-top: .9rem; }
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

/* Case header: identity, key facts, progress and the next step, kept together on the
   folder tab colour so it reads as the case's cover sheet. */
.st-key-case_head { background: #EDF1F3; border: 1px solid #D5DDE2; border-radius: 4px;
  padding: 1.1rem 1.25rem 1rem 1.25rem; gap: .5rem; }
.adj-case-id { font-size: .875rem; color: #5A6B78; margin: 0 0 .15rem 0; }
.adj-case-title { font-size: 2.25rem; font-weight: 700; letter-spacing: -0.015em;
  line-height: 1.12; margin: 0 0 .9rem 0; color: #1C2833; }
@media (max-width: 640px) { .adj-case-title { font-size: 1.75rem; } }
.adj-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
  gap: .6rem 1.5rem; margin: 0 !important; padding: 0 !important; }
.adj-facts div { min-width: 0; }
.adj-facts dt { font-size: .875rem; color: #5A6B78; margin: 0; }
.adj-facts dd { margin: .1rem 0 0 0; font-size: 1rem; color: #1C2833; font-weight: 600;
  overflow-wrap: anywhere; }
.adj-facts dd .adj-quiet { font-weight: 400; display: block; }

/* The rail: eight real steps, each the same width, labels wrap inside their step. */
.adj-rail { display: grid; grid-template-columns: repeat(10, minmax(0, 1fr)); gap: .3rem;
  list-style: none; padding: 0 !important; margin: 1rem 0 .25rem 0 !important;
  counter-reset: step; }
@media (max-width: 1100px) { .adj-rail { grid-template-columns: repeat(5, minmax(0, 1fr)); } }
@media (max-width: 520px) { .adj-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.adj-rail li { margin: 0 !important; }
.adj-rail li { counter-increment: step; font-size: .875rem; line-height: 1.25;
  padding: .35rem .45rem; color: #5A6B78; border-top: 3px solid #C9D3D9;
  hyphens: auto; }
.adj-rail li::before { content: counter(step) "  "; font-weight: 700; }
.adj-rail li.done { color: #1C2833; border-top-color: #1C2833; }
.adj-rail li.now { background: #000; color: #fff; border-top-color: #000; font-weight: 600; }
.adj-rail li.off { text-decoration: line-through; }

.adj-next { font-size: 1.05rem; margin: .4rem 0 0 0; padding-left: .75rem;
  border-left: 3px solid #4A3AA0; max-width: 72ch; }
.st-key-case_head [data-testid="stPageLink"] a { border: 1px solid #4A3AA0;
  border-radius: 4px; background: #FAFBFB; padding: .3rem .75rem; display: inline-flex;
  margin-left: .75rem; }
.st-key-case_head [data-testid="stPageLink"] a span { color: #4A3AA0; font-weight: 600; }

/* In-page contents for long pages. */
.adj-toc { display: flex; flex-wrap: wrap; gap: .25rem 1.25rem; list-style: none;
  padding: 0 !important; margin: .75rem 0 0 0 !important; font-size: .9375rem; }
.adj-toc li { margin: 0 !important; }
.adj-toc a { color: #4A3AA0; }

/* Text that is frozen for the record (a signed narrative): readable, not greyed out. */
.adj-doc { white-space: pre-wrap; background: #FFFFFF; border: 1px solid #D5DDE2;
  border-radius: 4px; padding: .9rem 1rem; line-height: 1.55; max-width: 80ch;
  color: #1C2833; margin: 0; font-size: 1rem; font-family: inherit; }

/* The AI Suggestion's match with the EDC: a table whose reasons wrap instead of being
   cut off, as a dataframe cell would cut them. */
.adj-match { border-collapse: collapse; width: 100%; font-size: .92rem; }
.adj-match th, .adj-match td { text-align: left; vertical-align: top; padding: .45rem .6rem;
  border-bottom: 1px solid #D5DDE2; }
.adj-match th { font-weight: 600; color: #52606D; }
.adj-match .why { color: #3E4C59; min-width: 24ch; }
.adj-match td:nth-child(2), .adj-match td:nth-child(4) { white-space: nowrap; }
.adj-match q { font-style: italic; }
/* On a phone each field becomes a block with its column names beside the values. */
@media (max-width: 640px) {
  .adj-match thead { display: none; }
  .adj-match tr { display: block; border-bottom: 1px solid #D5DDE2; padding: .4rem 0; }
  .adj-match td { display: block; border: 0; padding: .15rem 0; white-space: normal !important; }
  .adj-match td:not(:first-child)::before { content: attr(data-label) ": "; font-weight: 600;
    color: #52606D; }
}

/* The Narrative Edit on the Case record: removed and added lines, marked by word and
   by a left rule, not by colour alone. */
.adj-diff { padding: .3rem .6rem; margin: .2rem 0; border-left: 3px solid; max-width: 80ch;
  line-height: 1.5; color: #1C2833; }
.adj-diff.del { border-color: #A6342B; background: #FBEDEB; text-decoration: line-through; }
.adj-diff.add { border-color: #23704F; background: #EAF5EF; }

/* E-mail pop-ups (ui/mail_toasts.py): at the bottom right, wide enough that the task and
   case number are never cut off behind "view more". */
[data-testid="stToastContainer"] { top: auto !important; bottom: 1rem !important; }
[data-testid="stToast"] { width: 27rem; max-width: calc(100vw - 2rem); }

.adj-brand { margin: 0 0 .25rem 0; line-height: 1.3; }
.adj-brand strong { display: block; font-size: 1.05rem; color: #1C2833; }
.adj-brand span { font-size: .875rem; color: #5A6B78; }

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
    (State.QC_CHECKS, "Quality check"),  # with the narrative since P-39: one stop
    (State.READY_FOR_ADJUDICATION, "Ready for adjudication"),
    (State.ADJ_REVIEW, "Adjudication"),  # Flows 3 and 4 (P-41)
    (State.OUTCOME, "Outcome"),
    (State.CLOSED, "Closed"),
]
# States that sit on another state's step of the rail.
RAIL_STEP = {
    State.QC_REVIEW: State.QC_CHECKS,
    State.QUERY_OPEN: State.QC_CHECKS,
    State.NARRATIVE: State.QC_CHECKS,  # cases from before P-39
    State.NARRATIVE_REVIEW: State.QC_CHECKS,
    State.ADJ3_REVIEW: State.ADJ_REVIEW,
    State.CEC_MEETING: State.ADJ_REVIEW,
    State.MORE_DATA: State.ADJ_REVIEW,
    State.EXPORT: State.OUTCOME,
}
STATE_WORDS = workflow.STATE_WORDS  # shared with the Slack app (P-55)
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


# Coding details of an AE: shown under Technical details, not in the main record.
AE_CODES = {"pt_code", "soc_code", "meddra_version", "last_modified", "ae_id"}


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
    if re.fullmatch(r"[A-Z_]{4,}", value or ""):  # a code word, not an id like DEMO01-101
        return value.replace("_", " ").capitalize()
    return value or "Not recorded"


def rules_html(results: list[dict]) -> str:
    """Eligibility explanation as a nested list of met / not met rules, in words: the rule's
    description and the record's value ("Serious: No"), without rule codes."""
    labels = dict(AE_FIELDS)
    out = []
    for r in results:
        word = status("Met", "good") if r["passed"] else status("Not met", "bad")
        label = html.escape(r.get("description") or r.get("id") or "")
        if "group" in r:
            kind = "all of" if r["group"] == "all" else "any of"
            out.append(f"<li>{word}: {label} ({kind})<ul>{rules_html(r['children'])}</ul></li>")
        else:
            field = labels.get(r["field"], r["field"].replace("_", " ").capitalize())
            value = plain_value(str(r["actual"] if r["actual"] is not None else ""))
            out.append(
                f"<li>{word}: {label} <span class='adj-quiet'>"
                f"({html.escape(field)}: {html.escape(value)})</span></li>"
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


def brand(organisation: str) -> None:
    """The organisation and the product, as text at the top of the sidebar."""
    st.sidebar.html(
        f'<p class="adj-brand"><strong>{html.escape(organisation)}</strong>'
        "<span>Clinical event adjudication</span></p>"
    )


def synthetic_band() -> None:
    st.html(
        '<div class="adj-band" role="note"><strong>Synthetic data only.</strong> '
        "For demonstration; not for real patient data and not validated.</div>"
    )


SHORT_STATE = {s: label.lower() for s, label in LIFECYCLE} | {
    State.QC_REVIEW: "QC review",
    State.QUERY_OPEN: "query open",
    State.NARRATIVE_REVIEW: "narrative review",
    State.ADJ_REVIEW: "reviews",
    State.ADJ3_REVIEW: "adjudicator 3",
    State.CEC_MEETING: "committee",
    State.MORE_DATA: "more data",
    State.EXPORT: "exporting",
    State.WITHDRAWN: "withdrawn",
}


def short_state(state: State, role: str | None = None) -> str:
    """Compact form for the case picker: 'site review', 'submitted'."""
    if role == "site" and state in workflow.SITE_BLINDED:
        return "adjudication"  # ACC-04, P-45
    return SHORT_STATE.get(state, str(state))


def state_words(state: State, role: str | None = None) -> str:
    return workflow.state_words(state, role)


def flag_words(flags: list[str]) -> str:
    return ", ".join(FLAG_WORDS.get(f, f.replace("_", " ")) for f in flags)


def case_heading(case_id: str, title: str, facts: list[tuple[str, str]]) -> None:
    """Case number, event term and the key facts. Fact values are HTML (escaped by the
    caller, so a status word can carry its ink)."""
    items = "".join(
        f"<div><dt>{html.escape(label)}</dt><dd>{value}</dd></div>" for label, value in facts
    )
    st.html(
        f'<p class="adj-case-id">Case {html.escape(case_id)}</p>'
        f'<h1 class="adj-case-title">{html.escape(title)}</h1>'
        f'<dl class="adj-facts">{items}</dl>'
    )


# Ink for the case's status in its header: only where the state itself says something
# went well or needs attention; every other state is plain ink.
STATE_INK = {
    State.READY_FOR_ADJUDICATION: "good",
    State.QUERY_OPEN: "bad",
    State.CLOSED: "good",
    State.WITHDRAWN: "none",
}


def state_status(state: State, role: str | None = None) -> str:
    word = state_words(state, role)
    ink = STATE_INK.get(state)
    return status(word, ink) if ink else html.escape(word)


def toc(sections: list[tuple[str, str]]) -> None:
    """Links to the sections of a long page: (anchor, title)."""
    links = "".join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t in sections)
    st.html(f'<nav aria-label="On this page"><ul class="adj-toc">{links}</ul></nav>')


def frozen_text(text: str, label: str) -> None:
    """Read-only text kept for the record, at full contrast."""
    st.html(f'<pre class="adj-doc" aria-label="{html.escape(label)}">{html.escape(text)}</pre>')


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
