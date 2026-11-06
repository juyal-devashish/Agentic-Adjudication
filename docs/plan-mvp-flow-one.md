# Plan — Flow 1 (Intake) Proof of Concept

| | |
|---|---|
| **Implements** | [mvp-flow-1.md](mvp-flow-1.md) v0.1 (referred to below as "the spec") |
| **Goal** | A really simple, working proof of concept of Flow 1, ready for a stakeholder demo |
| **Status** | Step 0 done. Tick the boxes as steps are completed and committed |

---

## 1. What we are building

One demo, end to end, on a laptop with `docker compose up`:

> A synthetic AE file is dropped in the inbox → eligible rows become case folders with an explained result for every row → the site is notified → the site uploads a text PDF and a scan → PHI is removed destructively and verified by code → the site reviews, corrects and signs → a hashed handoff manifest for Flow 2 is written → the timeline shows a verified audit trail and the metrics page shows how much PHI was removed.

"Really simple" means: **all Must requirements of the spec, nothing more, until the core demo works.** Shoulds and Coulds become optional stretch steps after the demo is rehearsable.

## 2. Scope

### 2.1 Core (required for the demo)

| Area | Included | Spec |
|---|---|---|
| Storage | `CaseStore`, hash-chained `events.jsonl`, `case.json` cache, intake log, rebuild and verify tools | §5, F1-NF-03/04 |
| Intake | CSV import from `inbox/` + **Import now**, whole-file rejection, eligibility rules, evaluation log, idempotency, `source.changed`, related data, prior events | F1-01 to F1-07 |
| Documents | PDF (text and scanned), PNG, JPG, TIFF; dual extraction (text layer + OCR); Presidio + regex + context rules; allow-list; `dob_only` date policy; destructive redaction; metadata scrub; verification | §7.1 to §7.5, §7.8 |
| Site review | Previews, findings table, untick with reason, add terms, re-run, **Sign and submit** gated on verification | F1-20 to F1-24, F1-26 |
| Handoff | `flow2_manifest.json` with hashes; `handoff.flow2_ready` event | §11 |
| Notifications | JSON + `.eml` in `outbox/`, no clinical content | §10 |
| Measurement | Synthetic generator with ground truth; `deid_check.py` metrics | §13 |
| UI | Three Streamlit pages, banner, role switcher, case picker | §9 |

### 2.2 Stretch (only after the core demo is rehearsed, in this order)

| Stretch | Spec | Demo scenario |
|---|---|---|
| A. Manual AE entry and case withdrawal | F1-08, F1-11 | S4 |
| B. XLSX documents (including hidden sheets/columns) | §7.6 | S9 |
| C. DICOM header de-identification, then pixels | §7.7 | S10 |
| D. AI-1 as an extra detector (needs an approved API key) | §8 | S11 |
| E. File-type label per upload (F1-25), duplicate flag (F1-09), XLSX AE import | F1-25, F1-09, F1-02 | — |

### 2.3 Not in this POC

Everything from Flow 2 on; DOCX; the SQLite EDC adapter (F1-10); the `shift` and `redact_all` date policies (shown as options in config, not implemented unless time allows); hand-drawn redaction boxes; Mailpit (outbox only); languages other than English.

## 3. Decisions taken for the POC

From the spec's open questions (§17) and the gaps it leaves. Change any of these by editing this table first.

| # | Decision | Default used |
|---|---|---|
| P-01 | Date policy (Q-F1-01) | `dob_only` |
| P-02 | PHI definition (Q-F1-02) | EU personal data + HIPAA Safe Harbor list |
| P-03 | Known identifiers typed in by site (Q-F1-03) | Not in core; skipped |
| P-04 | Document languages (Q-F1-05) | English only |
| P-05 | Related EDC data (Q-F1-06) | Demographics without identifiers, medical history, con meds, labs |
| P-06 | AE import format | CSV only in core |
| P-07 | Study | One study, `DEMO01` |
| P-08 | Python tooling | Python 3.12, `uv` + `pyproject.toml`, `ruff` for lint and format, `pytest` |
| P-09 | Time in code | Clock injected everywhere, so tests use a fixed time |
| P-10 | Runner | One process, one case at a time; every step safe to re-run after a crash |
| P-11 | Demo reset | Add `tools/reset_demo.py` (not in the spec) to wipe runtime data and regenerate the corpus |

## 4. How we work

- One step at a time, in order. Each step lists its **deliverables**, **tests** and **done when** criteria. A step is done only when its criteria pass on a clean checkout.
- Work on a branch per step (`step-1-skeleton`, …), commit after each file or logical change, and merge to `main` when the step is done (see [AGENTS.md](../AGENTS.md)).
- Each step ends with something you can show: a command output, a page, or a metrics file.
- Tests never call an AI API, and all synthetic data comes from a fixed seed.

---

## 5. Steps

### Step 0 — Project setup

**Deliverables**
- [x] `pyproject.toml` (uv), dependencies pinned: streamlit, pydantic, jsonschema, pyyaml, filelock, pymupdf, pytesseract, pillow, presidio-analyzer, spacy (`en_core_web_lg`), bcrypt, faker, reportlab (for the generator), pytest, ruff
- [x] Package layout: `adjud/`, `ui/`, `tools/`, `tests/`
- [x] `.gitignore` for runtime data under `data/` (keep `data/config/`), `.env.example`
- [x] `Dockerfile` with Tesseract and the spaCy model; `docker-compose.yml` with `ui` and `runner`, `./data` mounted
- [x] Sample config: `data/config/users.yaml` (bcrypt hashes), `data/config/DEMO01/{study,eligibility_rules,related_data,deid_policy}.yaml`
- [x] Update the Commands table in AGENTS.md with the real commands

**Done when:** `uv run pytest` runs (zero tests is fine), `uv run ruff check` is clean and `docker compose build` succeeds.

### Step 1 — Storage skeleton

**Deliverables**
- [ ] `adjud/events.py`: `Event` model, canonical JSON, SHA-256 hash, genesis `prev_hash`
- [ ] `adjud/store.py`: `CaseStore` protocol + `FileCaseStore` (per-case `filelock`, temp file + atomic rename, files before events, never overwrite)
- [ ] `adjud/workflow.py`: Flow 1 states and transition table (spec §4), `apply_event()`, `replay()`; invalid transitions are refused before anything is written
- [ ] Intake log `data/intake/evaluations.jsonl` with the same hash chain
- [ ] `tools/rebuild_state.py` and `tools/verify_log.py` covering cases **and** the intake log

**Tests**
- Chain links correctly; editing any field of any event is detected; deleting or changing a referenced file is detected
- Deleting all `case.json` and rebuilding gives byte-identical files
- Illegal transitions are refused and leave the log unchanged
- A write interrupted between file and event leaves a consistent case (orphan file, no event)

**Done when:** all tests pass and both tools run against a hand-built sample case.

### Step 2 — Synthetic data generator

**Deliverables**
- [ ] `tools/make_synthetic.py --seed N`:
  - `edc_mock/` CSVs for ~20 subjects (demographics, medical_history, conmeds, labs, ae)
  - `inbox` AE file of ~30 rows mixing eligible and non-eligible, plus an **expected-results file**
  - a second AE file with one changed row (for S3) and a malformed file (for whole-file rejection)
  - per eligible event: discharge summary, ECG report, lab report as text PDFs with seeded fake PHI (names, DOB, address, phone, email, MRN), including in tables, headers and footers
  - scan variants of the same pages: 300 dpi, 150 dpi, skew/rotation/noise
  - a mixed PDF: text body with an image header containing the patient's name
  - clinical look-alikes (drug names, eponyms such as "Parkinson", admission dates) to measure over-redaction
  - `ground_truth.json`: every seeded identifier with its type, file and page
- [ ] Spreadsheet and DICOM variants are **not** generated in core (stretch B/C)

**Tests:** same seed gives identical output (by hash); ground truth matches what was written into the files.

**Done when:** one command produces the full corpus in under a minute.

### Step 3 — Intake

**Deliverables**
- [ ] `adjud/rules.py`: interpreter for `equals`, `not_equals`, `in_list`, `not_in_list`, `exists`, `on_or_after_field`, `before_field`, `within_days_of_field`, with `all`/`any` nesting. No `eval()`. Every rule result has `passed`, `actual`, `expected`
- [ ] `adjud/intake.py`: validate the CSV against the template (spec.md §11.2) and reject the whole file with a row-level report in `processed/`; evaluate every row into the intake log; create cases; idempotency on (study, subject, AE ID); newer `last_modified` → new snapshot, rules re-run, `source.changed`
- [ ] Related data from `related_data.yaml`; prior similar events (same PT or SOC within 365 days)
- [ ] `adjud/notify.py`: JSON + `.eml` to `outbox/`, study / case ID / action / link only
- [ ] `adjud/runner.py`: loop (10 s tick, inbox every 5 min, both configurable); takes cases from EVENT_CREATED to AWAITING_DOCUMENTS
- [ ] UI: `ui/app.py` sidebar (banner, role switcher, case picker) and **Page 1** (upload, Import now, per-row result with rule-by-rule reasons, cases flagged `source.changed`)

**Tests:** each rule operator passing and failing; nested `all`/`any`; eligibility matches the generator's expected results 100%; re-import creates no cases; changed row gives `source.changed` and nothing is withdrawn; malformed file imports nothing; notifications contain no clinical fields.

**Done when:** scenarios **S1, S2, S3** work in the UI.

### Step 4 — Text PDF de-identification (core of the POC)

**Deliverables**
- [ ] `adjud/deid/` split into small modules: `validate` (type from content, limits, encrypted → `deid.failed`), `extract` (PyMuPDF words with boxes), `detect` (Presidio + custom regex from `deid_policy.yaml` + label-context rules), `policy` (allow-list, `dob_only`), `redact` (redaction annotations + `apply_redactions` with images removed under boxes), `scrub` (`Document.scrub()` + clean save), `verify` (re-extract from output, re-run all detectors, check metadata empty, search seeded identifiers for synthetic data)
- [ ] One report per file in `documents/reports/`; one summary event per step
- [ ] Runner takes cases from DEIDENTIFYING to AWAITING_SITE_CONFIRMATION (or back to AWAITING_DOCUMENTS on `deid.failed`); a crash mid-file resumes by redoing that file
- [ ] `tools/deid_check.py`: run the corpus, write `data/metrics/<run>.json` (recall per identifier type, document variant and detector; over-redaction; verification catch rate; metadata residuals; time per page)

**Tests:** underlying characters are gone (not just covered) after redaction; metadata, attachments, links and JavaScript are removed; allow-listed terms survive; a planted residual is caught by verification; encrypted PDF fails cleanly.

**Done when:** `deid_check.py` shows **100% of seeded identifiers removed from text PDFs and zero metadata residuals** (spec §15 gate). Don't loosen the detectors' test thresholds to get there.

### Step 5 — Scans and mixed PDFs

**Deliverables**
- [ ] Normalise: PNG/JPG/TIFF → PDF pages; orientation correction from Tesseract OSD
- [ ] OCR every page at 300 dpi, map word boxes to PDF coordinates, merge with text-layer findings
- [ ] Scanned and image pages are flattened with black boxes and rebuilt as image-only pages (no hidden text layer)
- [ ] Verification re-OCRs the output
- [ ] `deid_check.py` reports scan and mixed-PDF metrics separately

**Tests:** the name in the mixed PDF's image header is found and removed; output scan pages contain no text layer; text-PDF gate from step 4 still holds.

**Done when:** scan recall and over-redaction per variant are measured and written to metrics (reported, not gated), and **S6** works from the runner.

### Step 6 — Site review, submission and handoff

**Deliverables**
- [ ] `adjud/signatures.py`: bcrypt demo-password check; signature records signer, meaning, time and hashes of the signed files
- [ ] **Page 2**: case header; upload area with file status (*processing / ready for review / blocked / verified*); page previews with outlined redactions; findings table (page, category, detector, confidence, partly masked text); untick with mandatory reason; add terms (all pages and files); delete file; each change re-runs redaction and verification for the affected file
- [ ] **Sign and submit** enabled only when every file is verified; `submission.signed` → SUBMITTED
- [ ] Handoff: `handoff/flow2_manifest.json` per spec §11 (nothing from `original/`), `handoff.flow2_ready`, notifications to site and Safety Specialist
- [ ] **Page 3**: case list by state; timeline with "chain verified ✓"; source data and eligibility result; manifest and redacted-file downloads; notifications; latest de-identification metrics

**Tests:** submit refused while any file is blocked; untick without a reason is refused; every correction is an event; every hash in the manifest matches its file; the manifest never references `original/`.

**Done when:** scenarios **S5, S7, S8, S12** work in the UI.

### Step 7 — Demo hardening and rehearsal

**Deliverables**
- [ ] `tools/reset_demo.py`: wipe runtime data, regenerate the corpus, pre-load the inbox
- [ ] `docker compose up` runs the full demo on a clean machine
- [ ] Short `README.md`: what this is, synthetic-data warning, how to run, the demo script
- [ ] Demo script (§6 below) rehearsed end to end at least twice from a reset
- [ ] Final metrics run saved for page 3

**Done when:** the core demo runs from reset to the last scenario without touching code, in about 20 minutes.

### Stretch steps (A–E)

Each follows the same pattern: deliverables, tests, the matching scenario, and a fresh rehearsal. The spec sections are listed in §2.2. AI-1 (D) runs only with an approved key, only on synthetic data, through `adjud/ai/gateway.py`, and its value is shown by a `deid_check.py` run with AI off vs on.

---

## 6. Demo script (core)

| Order | Scenario | Role | What the audience sees |
|---|---|---|---|
| 1 | S1 Import mixed AE file | Study admin | Eligible vs not, rule-by-rule reasons |
| 2 | S2 Import the same file again | Study admin | No new cases |
| 3 | S3 Import an updated row | Study admin | `source.changed` flag; nothing withdrawn |
| 4 | S5 Upload text PDF, review, submit | Site user | Redactions, findings, signature, handoff manifest |
| 5 | S6 Upload a poor scan | Site user | OCR redaction, image-only output page |
| 6 | S7 Residual name caught | Site user | Submit blocked; site adds the term; passes |
| 7 | S8 Untick a drug name | Site user | Correction with reason in the timeline |
| 8 | Metrics | Safety Specialist | Recall per document type, metadata residuals = 0 |
| 9 | S12 Tamper and rebuild | Study admin | `verify_log.py` detects the edit; state rebuilt |

Stretch scenarios S4, S9, S10 and S11 slot in after step 7 of the script when built.

## 7. Success check before the demo

Mapped to spec §15:

- [ ] Eligibility matches the expected-results file 100%, with a reason on every non-eligible row
- [ ] Re-import creates no cases; updated rows are flagged
- [ ] Text PDFs: 100% of seeded identifiers removed, confirmed by verification
- [ ] Zero metadata residuals in every output file
- [ ] Scan recall and over-redaction measured and shown (not gated)
- [ ] No file can be submitted while verification finds a residual
- [ ] Every hash in the handoff manifest matches its file
- [ ] The core demo script runs from reset

## 8. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Presidio name detection misses or over-flags on clinical text | Text-PDF gate fails, or drug names redacted | Label-context rules, allow-list in `deid_policy.yaml`; tune config, never the test |
| OCR box alignment drifts on skewed scans | Redaction box misses part of a word | Pad boxes; verification re-OCR catches residuals and blocks submission |
| Tesseract and spaCy model setup differs between laptop and Docker | Demo fails on the day | Rehearse only through `docker compose up` |
| Processing too slow for a live demo | Long waits on stage | Measure time per page from step 5; pre-process large scans before the demo; keep demo files small |
| Streamlit reruns cause double actions | Duplicate events | Every UI write goes through `CaseStore`, which refuses illegal transitions; disable buttons after use |
