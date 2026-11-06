# Flow 1 ingestion — test cases

Test cases for the Flow 1 MVP (flow1-mvp-spec.md). Requirement IDs (F1-xx, DOC-xx) refer to that spec and to spec.md.

There are four groups:

- **TC-RUL** checks the eligibility rule interpreter.
- **TC-INT** checks intake: import, validation, eligibility, idempotency, change detection, EDC lookups, audit log and notifications.
- **TC-DOC** lists acceptance cases for the document de-identification pipeline, one per corpus document, scored by `tools/deid_check.py`.
- **TC-COR** and **TC-HAR** check the test kit itself. They prove the seeded PHI is really there and the scorer cannot be fooled.

"Automated" names the pytest function. Run everything with `pytest`. Add `-m "not ocr"` to skip the slower OCR tests.

---

## TC-RUL — eligibility rules

| ID | Case | Input | Expected | Maps to | Automated |
|---|---|---|---|---|---|
| TC-RUL-01 | Equality rule | serious = Y / N | Pass / fail; result shows actual and expected | F1-03 | `test_rul01_equals` |
| TC-RUL-02 | Missing value | pt_code empty | Rule fails with reason "missing value" (never silently passes) | F1-03 | `test_rul02_missing_value_fails_with_reason` |
| TC-RUL-03 | Unknown list in config | in_list: nope | Configuration error raised | F1-03 | `test_rul03_unknown_list_is_config_error` |
| TC-RUL-04 | Date boundary | onset = first dose; onset one day before; first dose missing | Pass; fail; fail with date reason | F1-03 | `test_rul04_on_or_after_boundary` |
| TC-RUL-05 | `any` group | Before first dose, FATAL vs RECOVERED | Eligible via R3b; otherwise fails as R3 with both child results | F1-03 | `test_rul05_any_group_explains_children` |
| TC-RUL-06 | Malformed rule | Two operators; no top-level group | Configuration error raised | F1-03 | `test_rul06_malformed_rule_rejected` |
| TC-RUL-07 | Version recorded | Any evaluation | Result carries rule set name and version | INT-05 | `test_rul07_result_records_rule_set_and_version` |
| TC-RUL-08 | Day window | 30 vs 31 days | Pass / fail | F1-03 | `test_rul08_within_days` |
| TC-RUL-09 | Study rule file | `config/DEMO01/eligibility_rules.yaml` | Every rule has an ID and description and evaluates | F1-03 | `test_rul09_study_rules_file_is_well_formed` |

## TC-INT — intake pipeline

Input files are in `inbox_scenarios/`. Expected values are in `expected/intake_expected.json`. The standard sequence is 01 → 02 → 03 → 06 → 07 → 09.

| ID | Case | Input | Expected | Maps to | Automated |
|---|---|---|---|---|---|
| TC-INT-01 | Initial import, mixed eligibility | 01_initial.csv (22 rows) | 11 cases created, 11 not eligible; every key's eligibility and failed rules exactly as expected, including: onset before dose but fatal (eligible), onset on first-dose day (eligible), uncoded term (R2 fails), lowercase/whitespace flags | F1-01, F1-03, F1-05 | `test_int01_initial_import_eligibility` |
| TC-INT-02 | Every decision explainable | 01_initial.csv | 22 evaluation log entries, each with rule set, version and rule-by-rule explanation | F1-03 | `test_int02_every_record_logged_with_explanation` |
| TC-INT-03 | Idempotent re-import | 02_reimport_identical.csv after 01 | 0 new cases, 22 unchanged | F1-04 | `test_int03_reimport_is_idempotent` |
| TC-INT-04 | Updates | 03_updates.csv after 01 | AE-001 changed (still eligible); AE-006 changed, flagged `no_longer_eligible`, **not withdrawn**; AE-014 unchanged; AE-008 newly eligible → case created; AE-005 with older timestamp ignored as stale (snapshot untouched, event logged); AE-023 new case | F1-04, INT-07 | `test_int04_updates_change_detection` |
| TC-INT-05 | Invalid files rejected whole | 04 (missing column), 05 (bad rows), 10 (semicolon) | Status REJECTED; nothing imported or logged; errors include the expected row, column and code | F1-02 | `test_int05_invalid_files_rejected_whole` |
| TC-INT-06 | Row-level error report | 05_invalid_rows.csv | Errors on rows 3–8 only: INVALID_DATE, INVALID_VALUE, UNKNOWN_SUBJECT, DUPLICATE_KEY, MISSING_VALUE, WRONG_STUDY; valid row 2 has none | F1-02 | `test_int06_invalid_rows_valid_row_has_no_error` |
| TC-INT-07 | XLSX import | 06_xlsx_format.xlsx | Accepted; Excel date cells and text dates both read as ISO dates; 2 cases, 1 not eligible | F1-02 | `test_int07_xlsx_with_excel_and_text_dates` |
| TC-INT-08 | Possible duplicate | 07_duplicate_candidate.csv | Case created and flagged `possible_duplicate` of AE-019 | F1-09 | `test_int08_possible_duplicate_flagged` |
| TC-INT-09 | Header-only file | 08_header_only.csv | Accepted, 0 records | F1-02 | `test_int09_header_only_file` |
| TC-INT-10 | UTF-8 with BOM | 09_utf8_bom.csv | Accepted; BOM not part of the first column name | F1-02 | `test_int10_utf8_bom` |
| TC-INT-11 | Manual entry | manual_entry_valid.json; manual_entry_missing_reason.json | Case created with source MANUAL, actor and reason recorded; without reason → REJECTED, MISSING_REASON | F1-08 | `test_int11_manual_entry` |
| TC-INT-12 | Related data filters | Case for AE-001 | Medical history 2; con meds ongoing at onset = Aspirin, Clopidogrel (end date equal to onset included); labs within ±30 days and in list = 2 × troponin T, HbA1c; demographics without identifiers | F1-06 | `test_int12_related_data_filters` |
| TC-INT-13 | Prior similar events | Cases AE-001, 002, 003, 004, 027 | Same PT within 365 days, or same SOC; 365 days included, 366 excluded; later events excluded; match type recorded | F1-07 | `test_int13_prior_events` |
| TC-INT-14 | Case audit log integrity | Any case | Hash chain verifies; editing one line is detected at that line | AUD-01, AUD-04 | `test_int14_case_log_chain_and_tamper_detection` |
| TC-INT-15 | Evaluation log and rebuildable index | Full sequence | Evaluation log chain verifies; index rebuilt from the log equals the saved index | F1-NF-04 | `test_int15_evaluation_log_chain_and_index_rebuild` |
| TC-INT-16 | New case state and site notice | 01_initial.csv | State AWAITING_DOCUMENTS; `documents_requested` notification to `site:<id>` | F1-05, §10 | `test_int16_new_case_state_and_site_notification` |
| TC-INT-17 | Notifications carry no clinical content | Full sequence | No event term, onset date or subject ID in any outbox file | NOT-02 | `test_int17_notifications_carry_no_clinical_content` |
| TC-INT-18 | Normalisation and Unicode | 01_initial.csv | " y " → Y, "fatal" → FATAL; non-ASCII term stored exactly | F1-02 | `test_int18_values_normalised_and_unicode_preserved` |
| TC-INT-19 | No patient identity in the store | Full sequence | No synthetic name, MRN, phone, e-mail or address anywhere in case files, logs or outbox | NFR-PRV-03 | `test_int19_no_patient_identity_in_store` |
| TC-INT-20 | Runner inbox handling | Two files in an inbox folder | Both processed, moved to processed/, each with a JSON report | F1-01 | `test_int20_process_inbox_moves_files_and_reports` |
| TC-INT-21 | End-to-end totals | Full sequence | 17 cases in total | F1-01 to F1-09 | `test_int21_full_sequence_totals` |

## TC-DOC — document de-identification acceptance cases

Run your pipeline on every file in `documents/`, write outputs to `<out>/<case_key>/<expected_output>`, then run:

```bash
python tools/deid_check.py --kit . --redacted <out> --ocr --gate --out metrics/run.json
```

**Gate (pass/fail):**

- every must-redact item in text PDFs is removed;
- no identifier remains in metadata or properties;
- documents that must be rejected produce no output.

All other rates are measured and reported, as flow1-mvp-spec.md §15 requires.

| ID | Doc | File | Variant | Expected handling | What it tests | Gate? |
|---|---|---|---|---|---|---|
| TC-DOC-01 | D01 | discharge_summary.pdf | text PDF, 2 pages | Process | Identifiers in banner, body, table, footer on every page; next of kin; e-mail; clinical dates, drug and eponym must be kept | Yes |
| TC-DOC-02 | D02 | ecg_report.pdf | text PDF | Process | Identifiers inside table cells; date followed by a time must be kept | Yes |
| TC-DOC-03 | D03 | troponin_results.xlsx | XLSX | Process | Name in hidden column, hidden sheet, formula result and document properties; phone in a cell comment; defined name pointing at hidden data; trial ID and values kept | Properties only |
| TC-DOC-04 | D04 | discharge_summary_scan.png | 300 dpi scan | Process → PDF | OCR-based redaction of a skewed, noisy scan | No (measured) |
| TC-DOC-05 | D05 | ct_head.dcm | DICOM | Process | Header tags (name, ID, birth date, address, accession, institution, referring physician), private tag holding a phone number, burned-in pixel text; study date and description kept | No (measured) |
| TC-DOC-06 | D06 | death_summary.pdf | text PDF, 3 pages | Process | Identifiers on the **last page footer only**; name, MRN and DOB in **PDF metadata**; coroner reference; GP name | Yes |
| TC-DOC-07 | D07 | autopsy_request_scan.pdf | image-only PDF, 200 dpi | Process | PDF with no text layer at all | No (measured) |
| TC-DOC-08 | D08 | summary_letter_lowquality.jpg | 150 dpi JPEG, skewed, blurred | Process → PDF | Poor-quality fax-like scan | No (measured) |
| TC-DOC-09 | D09 | referral_letter_mixed.pdf | text + image | Process | Identifiers **only in an image banner**; clean text layer. Text-only redaction must fail here | No (measured) |
| TC-DOC-10 | D10 | angiography_report_rotated.pdf | image PDF rotated 90° | Process | Orientation detection before OCR | No (measured) |
| TC-DOC-11 | D11 | clinic_letter.pdf | text PDF | Process | Identifiers in a "Re:" table; GP name and practice; e-mail in footer; eponym "Hodgkin" kept | Yes |
| TC-DOC-12 | D12 | entlassungsbrief.pdf | German text PDF | Process | Umlauts, German labels (geb., Pat.-Nr., Versichertennr.), dd.mm.yyyy dates; "Parkinson" kept | Yes |
| TC-DOC-13 | D13 | entlassungsbrief_scan_150dpi.png | German 150 dpi scan | Process → PDF | OCR of German text (install the `deu` Tesseract pack for fair results) | No (measured) |
| TC-DOC-14 | D14 | befund_verschluesselt.pdf | encrypted PDF | **Reject: ENCRYPTED** | Site told to send an unprotected file; no output | Yes |
| TC-DOC-15 | D15 | ekg_beschaedigt.pdf | corrupt PDF | **Reject: CORRUPT** | No output | Yes |
| TC-DOC-16 | D16 | roentgen_thorax.pdf | PNG with .pdf extension | Process as image → PDF | Type detected from content, mismatch warned; burned-in name, DOB, ID | No (measured) |
| TC-DOC-17 | D17 | notizen.txt | text file | **Reject: UNSUPPORTED_TYPE** | No output | Yes |

### Additional manual checks (UI, not automated here)

| ID | Case | Expected |
|---|---|---|
| TC-DOC-M1 | Site unticks a wrongly flagged drug name with a reason | Un-redacted in the output; reason in the case log |
| TC-DOC-M2 | Residual PHI found in verification | "Sign and submit" disabled; site sees the finding |
| TC-DOC-M3 | Site adds a term to redact | Term redacted on all pages of all files; re-verification runs |
| TC-DOC-M4 | Submission | Handoff manifest lists every redacted file with a hash matching the file |

## TC-COR — corpus integrity (positive controls)

| ID | Check | Automated |
|---|---|---|
| TC-COR-01 | Every indexed file and ground truth exists; counts match | `test_cor01_index_complete` |
| TC-COR-02 | Every text-layer item really is on its page or in metadata; image-only items are not in the text layer | `test_cor02_text_pdf_items_really_in_text_layer` |
| TC-COR-03 | The mixed PDF's identifiers exist only in the image | `test_cor03_mixed_pdf_identifiers_only_in_image` |
| TC-COR-04 | PHI really is in the PDF metadata of D06 | `test_cor04_pdf_metadata_seeded` |
| TC-COR-05 | XLSX hiding places really exist | `test_cor05_xlsx_hiding_places` |
| TC-COR-06 | DICOM header, private tag and burned-in flag present | `test_cor06_dicom_phi_locations` |
| TC-COR-07 | Encrypted, corrupt, mislabelled, unsupported and rotated files behave as described | `test_cor07_invalid_and_tricky_files` |
| TC-COR-08 | No synthetic identity appears in the mock EDC or import files | `test_cor08_edc_and_import_files_hold_no_identities` |
| TC-COR-09 | Phone, NHS and e-mail values come from reserved test ranges | `test_cor09_identifiers_use_reserved_ranges` |
| TC-COR-10 | No must-keep value overlaps a must-redact value | `test_cor10_keep_and_redact_values_do_not_collide` |
| TC-COR-11 | All EDC files reference known subjects | `test_cor11_edc_subjects_consistent` |
| TC-COR-12 | Regenerating gives byte-identical data files | `test_cor12_generator_is_deterministic` |

## TC-HAR — scoring harness

| ID | Check | Automated |
|---|---|---|
| TC-HAR-01 | No redaction → gate fails; every text-PDF identifier reported residual | `test_har01_null_redactor_fails_gate` |
| TC-HAR-02 | Correct text-layer, XLSX and DICOM-header redaction → gate passes; must-keep items preserved | `test_har02_oracle_passes_gate` |
| TC-HAR-03 | Without OCR, image content is "unverified", never "removed" | `test_har03_without_ocr_image_content_is_never_called_removed` |
| TC-HAR-04 | Output for a document that should be rejected fails the gate | `test_har04_leaked_output_for_rejected_document_fails_gate` |
| TC-HAR-05 | A missing output counts as failure | `test_har05_missing_output_counts_as_failure` |
| TC-HAR-06 | Reference intake checks reject encrypted, corrupt and unsupported files and spot the mislabelled PNG | `test_har06_reference_intake_checks` |
| TC-HAR-07 | With OCR, PHI left in scans is detected, including on the rotated page | `test_har07_ocr_finds_phi_left_in_scans` |
| TC-HAR-08 | With OCR, text-only redaction is shown to miss the image banner | `test_har08_text_only_redaction_misses_image_banner` |
| TC-HAR-09 | Matching rules (dates with times, spaced digits, DICOM names, token boundaries) | `test_har09_matching_rules` |