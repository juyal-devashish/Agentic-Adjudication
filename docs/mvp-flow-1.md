# Clinical Event Adjudication — Flow 1 (Intake) MVP Specification

| | |
|---|---|
| **Document** | flow1-mvp-spec.md |
| **Version** | 0.1 (draft for discussion) |
| **Date** | 5 November 2026 |
| **Parents** | spec.md v0.1 (requirement IDs such as INT-05, DOC-08) and mvp-spec.md v0.1 (storage design, UI approach) |
| **Covers** | Flow 1 of the workflow deck (slide 4) only: from AE in the EDC to a submitted, de-identified document package ready for Flow 2 |

---

## 1. Goal

Prove that intake can be automated end to end with a minimal UI and no database server:

```text
AE in EDC → Meets rule? ─No→ no case (reason recorded)
                │Yes
                ▼
Case created, EDC data pulled → Site notified → Site uploads documents
→ PHI redacted / DICOM de-identified (+ optional AI) → Site checks and signs
→ Handoff package for Flow 2
```

Flow 1 holds the highest technical risk in the whole module: messy source documents, PHI hidden in scans, images and metadata, and the need to *prove* it was removed. This MVP is designed to measure that risk with numbers.

### 1.1 Questions this MVP answers

1. Can configurable eligibility rules decide reliably which AEs become cases, and explain every "no"?
2. Can intake be idempotent and detect changes to an AE after the case exists?
3. What share of PHI is removed automatically from (a) text PDFs, (b) scanned documents, (c) spreadsheets, (d) DICOM, and can the result be verified by code?
4. How much does optional AI (AI-1) improve PHI detection on scans and images compared with rules and OCR alone?
5. Is the site's review-and-submit step usable with a very simple UI?
6. Can the output be packaged as a clear, hashed handoff that Flow 2 can consume?

## 2. Constraints

| Constraint | Consequence |
|---|---|
| Synthetic data only | No real patient data. Permanent "Synthetic data only" banner |
| No database server | Case folders and append-only event logs (§5), as in mvp-spec.md §4 |
| Minimal UI | Three Streamlit pages (§9) |
| No real authentication | Role switcher; demo password for signing |
| Single machine | `docker compose up` on a laptop or small VM |
| Not validated, not Part 11 | Mechanics are demonstrated, compliance is not claimed |
| AI optional | AI-1 off by default; only used with an approved API key, and only on synthetic data |

## 3. Scope

### 3.1 In scope

| Step on slide 4 | MVP coverage | Priority |
|---|---|---|
| AE entered in EDC | Mock EDC as CSV files; AE import file dropped in an inbox; manual entry form | Must (CSV), Should (manual) |
| Meets rule? | YAML eligibility rules with per-rule results stored for every record | Must |
| No event created | Non-eligible records logged with reasons in an intake log | Must |
| Event created, EDC data pulled | Case folder, AE snapshot, related data, prior similar events | Must |
| Site notified by email | Notification written to `outbox/` (optional Mailpit) | Must |
| Site uploads documents | PDF, PNG, JPG, TIFF (Must); XLSX (Should); DICOM (Should); DOCX (Could) | see §7 |
| PHI redacted, DICOM de-identified | Detection, destructive redaction, metadata scrub, verification | Must |
| AI finds PHI in scans and images | AI-1 as an extra detector, switchable | Could |
| Site checks and submits | Redaction review, corrections with reasons, demo e-signature | Must |
| Automatic → Flow 2 | Hashed handoff manifest; "documents received" notice to the Safety Specialist | Must |

### 3.2 Out of scope

Everything from Flow 2 onwards (QC checks, dossier, narrative, adjudication, outcome); real EDC connections; authentication and enforced access control; reminders and escalations; multi-study operation; production performance targets.

---

## 4. Case states (Flow 1 only)

| State | Meaning | Next action by |
|---|---|---|
| EVENT_CREATED | Case created; EDC data being pulled | Runner |
| AWAITING_DOCUMENTS | Site notified, waiting for uploads | Site |
| DEIDENTIFYING | Files being validated, converted, scanned for PHI and redacted | Runner |
| AWAITING_SITE_CONFIRMATION | Redacted files ready for the site to check | Site |
| SUBMITTED | Signed by the site; handoff package written. **End of Flow 1** | (Flow 2) |
| WITHDRAWN | Withdrawn by the Safety Specialist with a reason | — |

| From | Event | To | By |
|---|---|---|---|
| — | `case.created` | EVENT_CREATED | Runner |
| EVENT_CREATED | `edc.related_data_pulled`, `notification.queued` | AWAITING_DOCUMENTS | Runner |
| AWAITING_DOCUMENTS | `documents.uploaded` | DEIDENTIFYING | Site |
| DEIDENTIFYING | `deid.completed` | AWAITING_SITE_CONFIRMATION | Runner |
| DEIDENTIFYING | `deid.failed` (e.g. encrypted PDF) | AWAITING_DOCUMENTS | Runner (site told why) |
| AWAITING_SITE_CONFIRMATION | `documents.uploaded` / `redactions.changed` | DEIDENTIFYING | Site |
| AWAITING_SITE_CONFIRMATION | `submission.signed` | SUBMITTED | Site (only if verification passed) |
| any state before SUBMITTED | `case.withdrawn` | WITHDRAWN | Safety Specialist |
| any state | `source.changed` | (unchanged) | Runner; flagged for the Safety Specialist |

---

## 5. Storage

Same design as mvp-spec.md §4: no database server; each case is a folder whose source of truth is a hash-chained, append-only `events.jsonl`; `case.json` is a rebuildable cache; all code goes through the `CaseStore` interface; atomic writes and per-case file locks.

Flow 1 adds an **intake log** for records that never become cases, so every "no" stays explainable and auditable.

```text
data/
├── config/
│   ├── users.yaml
│   └── DEMO01/
│       ├── study.yaml
│       ├── eligibility_rules.yaml
│       ├── related_data.yaml          # which mock-EDC tables/fields to pull
│       └── deid_policy.yaml           # what counts as PHI, date policy, allow-list
├── edc_mock/                          # demographics.csv, medical_history.csv, conmeds.csv, labs.csv, ae.csv
├── inbox/
├── processed/                         # imported files + import_report.json
├── intake/
│   └── evaluations.jsonl              # every evaluated record, eligible or not (hash-chained)
├── cases/
│   └── DEMO01-0001/
│       ├── events.jsonl
│       ├── case.json
│       ├── source/
│       │   ├── ae_snapshot.json
│       │   ├── eligibility.json
│       │   ├── related_data.json
│       │   └── prior_events.json
│       ├── documents/
│       │   ├── original/              # quarantine (synthetic only)
│       │   ├── working/               # normalised PDFs, OCR output
│       │   ├── redacted/
│       │   └── reports/               # one redaction report per file
│       ├── ai/                        # AI-1 calls, if enabled
│       └── handoff/
│           └── flow2_manifest.json
├── outbox/
├── metrics/                           # de-identification measurement results
└── logs/
```

---

## 6. Intake requirements

| ID | Requirement | Priority | Maps to |
|---|---|---|---|
| F1-01 | The runner imports AE files from `inbox/` every 5 minutes (configurable); page 1 can also upload a file or trigger **Import now**. | Must | INT-02, INT-11 |
| F1-02 | Files must match the template in spec.md §11.2 (CSV or XLSX). An invalid file is rejected whole with a row-level report in `processed/`; nothing is partly imported. | Must | INT-02 |
| F1-03 | Each record is evaluated against `eligibility_rules.yaml`. The record, rule set version, each rule's result and the input values are written to `intake/evaluations.jsonl`, for eligible and non-eligible records alike. | Must | INT-05 |
| F1-04 | A record whose (study, subject, AE ID) already has a case does not create another. If its `last_modified` is newer, a new AE snapshot is stored, rules are re-run and a `source.changed` event flags the case for the Safety Specialist. Nothing is withdrawn automatically. | Must | INT-04, INT-06, INT-07 |
| F1-05 | An eligible record creates a case folder and stores the AE snapshot with its hash. | Must | INT-04 |
| F1-06 | Related data defined in `related_data.yaml` is pulled from `edc_mock/` for the subject (e.g. demographics without identifiers, medical history, concomitant medication, relevant labs). | Must | INT-08 |
| F1-07 | Prior similar events for the subject are listed using the configured rule (default: same MedDRA PT, or same SOC, within 365 days before onset). | Must | INT-09 |
| F1-08 | An authorised demo user can enter an AE manually on page 1 with a mandatory reason. It goes through the same rules and is marked `source = MANUAL`. | Should | INT-03 |
| F1-09 | Possible duplicates (same subject, overlapping dates, same PT) are flagged on the case. | Could | INT-10 |
| F1-10 | A `SqlViewAdapter` reading a SQLite file that imitates an EDC reporting database shows the database route works. (SQLite here plays the *external* EDC, not the module's own storage.) | Could | INT-01 |
| F1-11 | The Safety Specialist can withdraw a case with a reason before submission. | Should | INT-07 |

### 6.1 Eligibility rules

Rules are declarative and evaluated by a small interpreter (no `eval()`). Supported operators: `equals`, `not_equals`, `in_list`, `not_in_list`, `exists`, `on_or_after_field`, `before_field`, `within_days_of_field`, with `all` / `any` nesting.

```yaml
rule_set: DEMO01-ELIG
version: 1
all:
  - id: R1
    description: Serious adverse event
    field: serious
    equals: "Y"
  - id: R2
    description: Preferred term is in the adjudicable list
    field: pt_code
    in_list: adjudicable_pts
  - any:
      - id: R3a
        description: Onset on or after first dose
        field: onset_date
        on_or_after_field: first_dose_date
      - id: R3b
        description: Fatal outcome (always adjudicated)
        field: outcome
        equals: FATAL
lists:
  adjudicable_pts: ["10028596", "10042316", "10011906"]   # synthetic examples
```

Each evaluation result lists every rule with `passed`, `actual value` and `expected`, so page 1 can show in plain words why a record was or wasn't eligible.

---

## 7. Document de-identification

This is the core of the Flow 1 MVP.

### 7.1 Supported files

| Type | Priority | Handling | Output |
|---|---|---|---|
| PDF with text layer | Must | Text extraction + OCR of the rendered page (§7.3) | Redacted PDF |
| Scanned PDF, PNG, JPG, TIFF | Must | Converted to PDF pages, then OCR | Redacted image-only PDF |
| XLSX | Should | Cell-by-cell scan (§7.6) | Redacted XLSX |
| DICOM | Should (header), Could (pixels) | §7.7 | De-identified DICOM + preview PNG |
| DOCX | Could | Converted to PDF with LibreOffice, then as PDF | Redacted PDF |

Limits: 50 MB per file, 500 MB per DICOM study, 200 pages per PDF (configurable).

### 7.2 Pipeline

Each uploaded file goes through these steps. Each step's result is written to `documents/reports/<file>.json` and summarised in an event.

1. **Store original** in `original/` and record its SHA-256 hash.
2. **Validate:** file type from content (not extension); size and page limits; reject encrypted or password-protected files with a clear message to the site.
3. **Normalise:** images become PDF pages; rotation is corrected from OCR orientation detection.
4. **Extract text with positions** from both the text layer and OCR (§7.3).
5. **Detect PHI** with the detectors in §7.4, plus AI-1 if enabled.
6. **Apply the policy** in `deid_policy.yaml`: drop allow-listed findings, apply the date policy.
7. **Redact destructively** (§7.5).
8. **Scrub metadata** (§7.5).
9. **Verify** (§7.8). Any residual finding marks the file *blocked*.
10. **Render previews** (page images with redaction boxes outlined) for the site review screen.

### 7.3 Text extraction

PDFs often mix real text with embedded images (a scanned letterhead, a pasted lab report, a signature stamp). To avoid missing PHI inside images, every page is processed **both** ways:

- **Text layer:** words with coordinates from PyMuPDF.
- **OCR:** the page rendered at 300 dpi and run through Tesseract, giving words with bounding boxes that are mapped back to PDF coordinates.

Findings from both are combined. This costs extra OCR time but closes the most common gap. OCR language packs follow the study's document languages (Q-F1-05).

### 7.4 Detectors

| Detector | Finds | Notes |
|---|---|---|
| Presidio built-in recognisers | Person names, locations, phone numbers, email, dates, national ID formats | spaCy English model; more languages per Q-F1-05 |
| Custom regex recognisers | MRN and hospital number formats, postal codes, IBAN, insurance numbers | Configured in `deid_policy.yaml` |
| Context rules | Values next to labels like "Name:", "DOB", "Date of birth", "Patient ID", "Address" | Raises confidence; catches unusual formats |
| Known-value matching | Exact values the site types in on upload (optional): e.g. patient name, MRN | Sent nowhere; used only for matching and then discarded |
| AI-1 (optional) | PHI in scans and images that rules miss | §8 |

`deid_policy.yaml` also holds an **allow-list** so that clinically needed text is not redacted: the subject's study ID, site number, investigational product names, common medical terms and eponyms (e.g. "Parkinson", "Hodgkin"), and hospital department names.

### 7.5 Redaction and metadata

| Content | Method |
|---|---|
| Text-layer pages | PyMuPDF redaction annotations on every finding's area, then `apply_redactions` with image pixels also removed under the boxes. The underlying characters are deleted, not covered. |
| Scanned/image pages | Page image flattened with solid black boxes and rebuilt as an image-only page. No hidden text layer is kept. |
| PDF metadata and extras | `Document.scrub()` removes metadata, XMP, embedded files, attachments, JavaScript, links, annotations and hidden text; then a fresh save with garbage collection. |
| Image metadata | EXIF and other metadata dropped on conversion. |

### 7.6 Spreadsheets (XLSX)

- Every cell on every sheet, **including hidden sheets, rows and columns**, is scanned with the same detectors.
- Columns whose header matches an identifier label (Name, DOB, MRN, Address, Phone…) are redacted entirely.
- Redacted cells are replaced with `[REDACTED]`; formulas referencing them are replaced by values.
- Comments, document properties, external links and defined names are removed.
- The site reviews the result as a table on page 2.

### 7.7 DICOM

- **Header (Should):** de-identified using a tag list based on the DICOM PS3.15 Annex E Basic Application Level Confidentiality Profile, using pydicom. Private tags removed; UIDs replaced consistently within the case; patient name and ID replaced with the subject's study ID.
- **Dates (Should):** handled by the date policy (§7.9), e.g. shifted by a per-subject offset.
- **Pixels (Could):** if `BurnedInAnnotation` is `YES`, or OCR finds text in the image, the text regions are masked.
- A preview PNG of the first frame is shown to the site.

### 7.8 Verification

After redaction, each output file is checked again from scratch:

1. Text re-extracted from the output (text layer and fresh OCR) and all detectors re-run.
2. Metadata re-read and checked empty.
3. For synthetic test data, the generator's seeded-identifier list is also searched for.

Any finding blocks submission of that file. The site sees what was found and can redact it or replace the file.

### 7.9 Dates: decision needed

Dates are both identifying and clinically essential: adjudicators need admission, procedure and death dates. Redacting all dates would make the documents useless. The MVP supports three policies in `deid_policy.yaml`, defaulting to the first:

| Policy | Behaviour |
|---|---|
| `dob_only` (default) | Redact dates of birth (found by context: "DOB", "born", "Date of birth"); keep clinical dates |
| `shift` | Shift every date by a per-subject offset, consistently across all documents and the EDC data |
| `redact_all` | Redact all dates (HIPAA Safe Harbor style); shown for comparison only |

The final policy is a privacy and clinical decision (Q-F1-01).

### 7.10 Site review

| ID | Requirement | Priority | Maps to |
|---|---|---|---|
| F1-20 | The site sees each page with redactions outlined and a findings table (page, category, detector, confidence, partly masked text). | Must | DOC-10 |
| F1-21 | The site can untick a finding (reason required) to un-redact it, e.g. a drug name wrongly flagged. | Must | DOC-10 |
| F1-22 | The site can add terms to redact on all pages and files. Drawing boxes by hand is a Could (drawable canvas). | Must / Could | DOC-10 |
| F1-23 | Any change re-runs redaction and verification for the affected file. | Must | DOC-08 |
| F1-24 | The site can delete a file (before submission) or upload more. | Must | DOC-01 |
| F1-25 | The site labels each file's document type from the study's list. | Should | — |
| F1-26 | **Sign and submit** is enabled only when every file has passed verification. Signing records signer, meaning (*Submitted — documents complete and de-identified*), time and the hashes of all redacted files. | Must | DOC-11, SIG-02, SIG-03 |

---

## 8. AI-1: finding PHI in scans and images (optional)

| Aspect | Specification |
|---|---|
| Purpose | An **additional** detector for scanned and image pages. It never replaces rules and OCR |
| Switch | `study.yaml → ai.phi_detection: false` by default |
| Input | Rendered page image (and its OCR words) for pages that are scanned or contain images |
| Output (JSON schema) | List of findings: `page`, `category`, `text`, `bbox` (normalised 0–1), `confidence` |
| Validation | Schema-checked; boxes clipped to the page; up to 2 retries, then continue without AI and say so |
| Merging | AI boxes are added to rule findings; labelled "AI" in the findings table |
| Logging | Each call saved in `cases/<id>/ai/`: model, prompt version, input hash, output, latency |
| Data rule | Synthetic data only. With real data this function sends unredacted content to the provider, which spec.md NFR-PRV-04 and decision D-07 must cover first |
| Evaluation | Run the corpus with AI off and on; compare recall and false positives per document type (§13) |

---

## 9. User interface

Three Streamlit pages. The sidebar has the synthetic-data banner, the role switcher (Site user, Safety Specialist, Study admin) and a case picker.

### 9.1 Page 1 — EDC input

- Upload an AE file or press **Import now**.
- Import results: one row per record, *eligible / not eligible*, and the rule-by-rule explanation.
- Manual AE entry form (Should).
- Cases flagged with `source.changed` or as possible duplicates.

### 9.2 Page 2 — Site documents

- Case header: subject study ID, event term, onset date, what documents are requested.
- Upload area and the list of files with status: *processing / ready for review / blocked / verified*.
- Review panel per file: page preview with outlined redactions; findings table with tick boxes; add-term box; spreadsheet table view; DICOM preview and header before/after.
- **Sign and submit** button with the demo-password dialog.

### 9.3 Page 3 — Results

- Case list by state.
- Case timeline from `events.jsonl` with "chain verified ✓".
- Source data: AE snapshot, related data, prior events, eligibility result.
- The handoff manifest and download of redacted files.
- Notifications written for the case.
- **De-identification metrics** from the latest corpus run (§13), with AI off vs on when available.

---

## 10. Notifications

All written to `outbox/` as JSON and `.eml`, optionally sent to Mailpit. No clinical content: study, case ID, action and link only.

| Trigger | Recipient |
|---|---|
| Case created: documents requested | Site |
| Processing failed (e.g. encrypted file) | Site |
| Redacted files ready for review | Site |
| Submission received (acknowledgement) | Site |
| Documents received, ready for QC | Safety Specialist |
| Source AE changed after case creation | Safety Specialist |
| Case withdrawn | Site |

---

## 11. Handoff to Flow 2

On submission the runner writes `handoff/flow2_manifest.json` and appends `handoff.flow2_ready`. This file is the contract with Flow 2: everything Flow 2 needs, with hashes, and nothing from `original/`.

```json
{
  "case_id": "DEMO01-0001",
  "study": "DEMO01",
  "subject": "DEMO01-104-0007",
  "created_at": "2026-11-05T10:45:02Z",
  "ae_snapshot": { "path": "source/ae_snapshot.json", "sha256": "…" },
  "eligibility": { "path": "source/eligibility.json", "rule_set": "DEMO01-ELIG", "version": 1 },
  "related_data": { "path": "source/related_data.json", "sha256": "…" },
  "prior_events": { "path": "source/prior_events.json", "sha256": "…" },
  "documents": [
    {
      "path": "documents/redacted/discharge_summary.pdf",
      "sha256": "…",
      "document_type": "discharge_summary",
      "pages": 4,
      "redaction_report": "documents/reports/discharge_summary.json",
      "verification": "passed",
      "ai_used": false
    }
  ],
  "deid_policy_version": 1,
  "submission": {
    "signer": "Site User Demo",
    "meaning": "Submitted — documents complete and de-identified",
    "signed_at": "2026-11-05T10:44:58Z"
  }
}
```

---

## 12. Non-functional requirements

| ID | Requirement |
|---|---|
| F1-NF-01 | Starts with `docker compose up` (ui, runner, optional mailpit). No database, broker or cloud service. |
| F1-NF-02 | Processing time is measured and reported per page for text PDFs and scans; proposed target under 10 seconds per scanned page on a 4-core machine with AI off. |
| F1-NF-03 | Atomic writes and per-case locks; killing the runner mid-file never corrupts a case, and on restart processing resumes. |
| F1-NF-04 | `rebuild_state.py` and `verify_log.py` (mvp-spec.md §4.4) work for cases and for `intake/evaluations.jsonl`. |
| F1-NF-05 | No PHI or document text in application logs (only IDs, counts, timings). |
| F1-NF-06 | API keys only through environment variables. |
| F1-NF-07 | Rules, related-data list and de-identification policy are changed by config files, not code. |

---

## 13. Synthetic corpus and measurement

### 13.1 Generator

`tools/make_synthetic.py` creates the mock EDC, an AE import file and, for each eligible event, documents with **seeded fake PHI** and a ground-truth file listing every seeded identifier, its type and location.

| Variant | Why |
|---|---|
| Clean text PDFs (discharge summary, ECG report, lab report) | Baseline |
| The same pages printed to image: 300 dpi, then 150 dpi | Typical scans |
| Scans with skew, rotation, noise, faint stamps | Real-world fax and scanner quality |
| Mixed PDFs: text with an embedded image header containing the patient's name | Tests the dual extraction in §7.3 |
| Identifiers in tables, headers and footers | Common hiding places |
| Lab export spreadsheets, including a hidden column with names | Tests §7.6 |
| DICOM files with populated patient tags and burned-in text | Tests §7.7 |
| Clinical text that looks like PHI (drug names, eponyms, dates of admission) | Measures false positives |

### 13.2 Metrics

`tools/deid_check.py` runs the whole corpus and writes `metrics/<run>.json`:

- **Recall** per identifier type, document variant and detector (rules, OCR, AI-1).
- **Over-redaction:** share of allow-listed or clinical terms wrongly redacted.
- **Verification catch rate:** residuals found by step 9 that earlier steps missed.
- **Metadata residuals** (target: zero).
- **Time per page.**

Results are shown on page 3 and compared between AI off and AI on.

---

## 14. Demo scenarios

| # | Scenario | Shows |
|---|---|---|
| S1 | Import a mixed AE file | Eligible vs not, with reasons |
| S2 | Import the same file again | Idempotency: no new cases |
| S3 | Import an updated row for an existing case | `source.changed` flagged, nothing withdrawn |
| S4 | Enter an AE manually | Manual source marked and evaluated the same way |
| S5 | Upload a text PDF; review; submit | Full happy path to the Flow 2 handoff |
| S6 | Upload a poor-quality scan | OCR redaction with an image-only output page |
| S7 | Upload a file where verification finds a residual name | Submission blocked; site adds the term; passes |
| S8 | Site unticks a drug name flagged as a person | Correction with a reason, in the audit trail |
| S9 | Upload a spreadsheet with a hidden name column | Hidden content redacted |
| S10 | Upload a DICOM file | Header before/after; preview |
| S11 | Re-run the corpus with AI-1 on | Recall and false-positive comparison |
| S12 | Edit an event by hand; delete `case.json` files | Tamper detection; state rebuilt |

---

## 15. Success criteria

1. Eligibility results match the generator's expected list exactly (100%), and every non-eligible record shows its reason.
2. Re-importing the same file creates no new cases; updated rows are flagged.
3. **Text PDFs:** every seeded identifier removed (100%), confirmed by verification. This is the pass/fail gate.
4. **Metadata:** zero residuals in every output file.
5. **Scans, spreadsheets and DICOM:** recall and over-redaction measured and reported per variant. The figures inform the production approach and the AI-1 decision; they are not a pass/fail gate for the MVP.
6. No file can be submitted while verification finds a residual.
7. The handoff manifest is complete, and every hash in it matches the files.
8. Scenarios S1–S12 run in front of stakeholders.

---

## 16. Build order

1. **Skeleton:** `CaseStore`, event logs, intake log, hash chains, rebuild and verify tools (reusable for the full MVP).
2. **Synthetic generator** with ground truth for all variants in §13.1.
3. **Intake:** rules interpreter, inbox import, idempotency, change detection, related data, prior events, page 1.
4. **Text PDF de-identification:** detectors, policy, destructive redaction, scrub, verification, `deid_check.py`.
5. **Scans:** OCR, image-page redaction, dual extraction for mixed PDFs.
6. **Site review and submission:** page 2, corrections, signing, handoff manifest, notifications, page 3.
7. **Spreadsheets.**
8. **DICOM** header, then pixels if time allows.
9. **AI-1,** if API access is approved; AI off/on comparison.
10. **Demo rehearsal** with S1–S12.

Steps 1–8 need no AI access.

---

## 17. Open questions

| ID | Question | Default for the MVP |
|---|---|---|
| Q-F1-01 | Which date policy should production use: DOB only, shifted, or all redacted? | `dob_only`, with the others shown for comparison |
| Q-F1-02 | What identifiers count as PHI for the target studies (EU only, or US sites too)? | Combined EU personal data + HIPAA Safe Harbor list |
| Q-F1-03 | Should sites type in known identifiers (patient name, MRN) to improve matching? | Optional field, discarded after processing |
| Q-F1-04 | Which document types do sites typically send, and in which formats? | The list in §13.1 |
| Q-F1-05 | Which languages are source documents in? | English only for the MVP |
| Q-F1-06 | Which related EDC data should be pulled for adjudication? | Demographics (no identifiers), medical history, con meds, relevant labs |
| Q-F1-07 | Will IT approve a Claude API key for synthetic data? | Build without AI; add AI-1 if approved |