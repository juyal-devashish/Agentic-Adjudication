# UI design plan — Flow 1 POC

Applies [frontend-design-skill.md](frontend-design-skill.md) to the Streamlit UI.

## Brief, as understood

- **Subject:** the intake desk of a clinical events committee. Adverse-event records come in, sites send hospital paperwork, identifiers are blacked out, the site signs, and a sealed package goes on to adjudication.
- **Audience:** site coordinators (occasional users who need the next step spelled out), a Safety Specialist, a study admin, and stakeholders watching a demo.
- **Primary job:** move a case from "AE arrived" to "signed, de-identified package", with proof at every step.
- **The subject's own vernacular:** the redaction bar, controlled-document stamps, case folders, signatures, audit trails, hospital forms.

## Tokens

### Color

| Name | Hex | Use |
|---|---|---|
| Form | `#EDF1F3` | Page background: the cool blue-grey of carbonless hospital form paper |
| Sheet | `#FAFBFB` | Inputs, tables, widget surfaces |
| Folder | `#DDE5E9` | Sidebar, the "case folder" the user is working in |
| Ink | `#1C2833` | Text: blue-black ballpoint |
| Stamp violet | `#4A3AA0` | Primary actions, links, focus: indelible stamp ink |
| Redaction | `#000000` | Reserved: the synthetic-data band, the current lifecycle step, redaction bars. Nothing else is true black |

Status inks, used only as text and thin marks: verified `#23704F`, needs attention `#A6342B`, waiting `#86651A`.

### Type

One family: **Public Sans**, the US Web Design System typeface drawn for government forms, self-hosted (OFL). Headings 700 with slight negative tracking, body 400. Scale from *The Elements of Typographic Style*: 14 / 16 / 21 / 24 / 36 px. Tabular figures for dates, counts and hashes, instead of a monospace face. Running text blocks at most 72 characters wide.

### Layout

The case is a folder on a desk. The sidebar is the folder tab (who you are, which case). The main area is the case sheet, left-aligned, max ~1180 px:

```text
┌ folder (sidebar) ─┐ ┌ case sheet ─────────────────────────────────────────────┐
│ ███ Synthetic data │ │ DEMO01-0001                                             │
│     only           │ │ Myocardial infarction, onset 14 Mar 2026                │
│ You are            │ │ Subject DEMO01-101-0001 at site 101                      │
│ [Site user     ▾]  │ │                                                         │
│ [Site 101 Coord ▾] │ │ 1 Case created  2 Documents  ███ 3 De-identifying ███   │
│ Case               │ │ 4 Site review  5 Submitted                              │
│ [DEMO01-0001   ▾]  │ │                                                         │
│                    │ │ Next: review the redacted pages, then sign and submit.  │
│ AE import          │ │ ─────────────────────────────────────────────────────── │
│ Documents          │ │ Files … Review … Sign and submit                         │
│ Case record        │ │                                                         │
└────────────────────┘ └─────────────────────────────────────────────────────────┘
```

### Principles

1. **Black means redacted or decisive.** True black appears only where something is blacked out or where the case is right now. That bar is the one memorable element; everything else stays quiet.
2. **Every screen says what to do next**, for the person viewing it, in one sentence.
3. **Plain names.** States are shown in the user's words ("Waiting for site review"), never as enum names.
4. **Evidence is quiet.** Hashes, timestamps and IDs are small and grey: there when an auditor looks, not shouting.

## Review against the brief and the generic defaults

| Default to avoid | Where it crept into the first draft | Revision |
|---|---|---|
| SaaS card kit, "big number + small label" tiles | Page 1 used five `st.metric` tiles for an import; page 2's header used four | Import results become one sentence ("22 records: 11 new cases, 11 not eligible"). The case header is a document heading, not tiles |
| Meta strings joined with middle dots | Nav titles "1 · EDC input"; case picker "DEMO01-0001 · STATE" | Nav uses task names; the picker shows the case and its plain-language state in words |
| ALL-CAPS labels | "SYNTHETIC DATA ONLY", raw `AWAITING_SITE_CONFIRMATION` everywhere | Sentence case throughout; enum names mapped to plain words |
| Emoji as status | ✅ ⛔ ❌ ⏳ in the file table | Status words in status inks |
| Generic blue or teal "health" accent | (first instinct for a clinical tool) | Stamp violet: specific to signing and stamping documents |
| Monospace for small data labels | — | Tabular figures in Public Sans; hashes shortened and grey |
| Numbered markers used as decoration | — | Numbers only on the lifecycle rail, which really is a sequence |

Motion: none on load. Only Streamlit's own feedback on user actions. `prefers-reduced-motion` is respected.

## Copy rules for this UI

- Buttons name the action, and the result uses the same word: "Import files" → "Imported 01_initial.csv"; "Sign and submit" → "Signed and submitted".
- Errors say what happened and what to do, without apologising: "This file is password-protected. Send an unprotected copy."
- Empty states invite the next action: "No documents yet. Upload the discharge summary, ECG report and lab results."

## Review notes from building it

Screenshots (`uv run python -m tools.screenshots --out <dir>`) were taken after each pass, at 1440 and 390 px width.

- **Pass 1** found a real bug: with the pages in `ui/pages/`, Streamlit's legacy multipage mode took over on a direct link or a browser refresh and crashed the page. The pages now live in `ui/views/` with explicit URLs (`/import`, `/documents`, `/record`).
- Two buttons said "Upload" (the file picker's and the form's). The form's button became "Add to case".
- The rail was indented by Streamlit's list styles, the sidebar case name was cut off, and the "200MB" picker hint contradicted the 50 MB limit. All three are fixed.
- **Pass 2** removed the leftover system vocabulary: `Dob`/`Mrn`, `UNSUPPORTED_TYPE` in the reasons, raw AE JSON, user IDs in the audit trail, times without a timezone.
- **Pass 3:** on a phone the forced-open sidebar covered the page, so it is now "auto". An empty "Type" column is hidden, and the "Found by" column is dropped as a system detail. That was the one accessory removed.

## Notes for future passes

- Streamlit owns most markup; CSS targets only stable `data-testid` hooks plus our own HTML blocks, and stays in `ui/theme.py`.
- Screenshots of each page are taken with Playwright (`tools/screenshots.py`) for review.
