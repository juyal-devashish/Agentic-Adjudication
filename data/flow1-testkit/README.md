# Flow 1 synthetic test kit

Synthetic data and test cases for the Flow 1 (intake) MVP described in flow1-mvp-spec.md.

**Everything here is synthetic.**

- Patient identities are invented. Phone numbers come from UK drama ranges (01632 960xxx, 07700 900xxx), insurance numbers from the NHS test range (999 xxx xxxx), and e-mail addresses use example.org.
- Codes are synthetic (9xxxxxxx). They are **not MedDRA**.
- Hospital names and the study drug (Zelvoran) are fictional.

## What is in the kit

```text
config/              Study configuration: eligibility rules, related-data rules, de-identification policy, demo users,
                     synthetic dictionary
edc_mock/            The mock EDC (demographics, AEs incl. history, medical history, con meds, labs). No names anywhere.
inbox_scenarios/     AE import files for each intake scenario (CSV, XLSX, manual-entry JSON)
expected/            Expected intake results for every scenario
documents/           17 source documents for 6 cases, with seeded fake PHI
ground_truth/        One file per document listing every seeded identifier and must-keep value, plus corpus_index.json
identities/          The synthetic identities behind the documents (to check they never leak)
adjud/               Reference intake pipeline (file-based, no database), so the intake tests run end to end
tools/               make_synthetic.py (generator), deid_check.py (scoring harness), reference_redactors.py, textmatch.py
tests/               53 pytest tests (rules, intake, corpus integrity, harness)
metrics/             Reference run of the harness on the "oracle" redactor, for comparison
TEST_CASES.md        The test case catalogue
```

## Quick start

```bash
pip install -r requirements.txt          # plus system Tesseract: apt install tesseract-ocr tesseract-ocr-deu
pytest                                   # all tests, about 45 s with OCR
pytest -m "not ocr and not slow"         # fast subset, no Tesseract needed
```

## The data

### Subjects and cases

There are 20 subjects at sites 101, 102 (UK-style documents) and 104 (German documents). The initial import of 22 AEs creates 11 cases. Documents exist for six of them:

| Case key | Event | Documents | Why it is interesting |
|---|---|---|---|
| DEMO01-101-0001_AE-001 | Myocardial infarction | D01–D03 | Text PDFs with PHI everywhere; spreadsheet with hidden PHI; prior events by PT and SOC; related-data filters |
| DEMO01-101-0002_AE-002 | Ischaemic stroke | D04–D05 | 300 dpi scan; DICOM with burned-in text; prior event exactly 365 days earlier |
| DEMO01-102-0003_AE-003 | Sudden cardiac death | D06–D07 | PHI in PDF metadata and last-page footer only; image-only PDF |
| DEMO01-102-0004_AE-005 | MI before first dose, fatal | D08–D09 | Eligible only via the "fatal" rule; low-quality JPEG; identifiers only in an image banner |
| DEMO01-101-0005_AE-006 | Unstable angina | D10–D11 | Onset on the first-dose day; page rotated 90°; later downgraded to non-serious |
| DEMO01-104-0006_AE-007 | TIA (German site) | D12–D17 | German text and scan; encrypted, corrupt, mislabelled and unsupported files |

### Import scenarios

| File | Scenario |
|---|---|
| 01_initial.csv | 22 rows covering every rule branch and edge case |
| 02_reimport_identical.csv | Same file again: nothing new |
| 03_updates.csv | Correction, downgrade, unchanged row, newly eligible row, stale row, new AE |
| 04_invalid_missing_column.csv | Required column missing |
| 05_invalid_rows.csv | Six different row errors and one valid row |
| 06_xlsx_format.xlsx | Excel import with real date cells and a text date |
| 07_duplicate_candidate.csv | Overlapping event for the same subject and term |
| 08_header_only.csv | No data rows |
| 09_utf8_bom.csv | CSV saved by Excel as "UTF-8" (with BOM) |
| 10_semicolon_delimited.csv | European-Excel CSV with semicolons |
| manual_entry_*.json | Manual entry with and without a reason |

### Ground truth

Each item in `ground_truth/Dxx.json` has:

- `value`: the exact seeded text;
- `category`, e.g. PATIENT_NAME, DOB, MRN, STAFF_NAME, CLINICAL_DATE;
- `requirement`: `must_redact`, `should_redact` (staff names and facilities) or `must_keep` (clinical dates, drug, eponyms, lab values, trial ID);
- `channel`: where the scorer looks, e.g. `page:2`, `metadata`, `hidden`, `comments`, `properties`, `dicom_header` or `pixels`;
- `locations`: banner, footer, table, image_header, burned_in and so on.

For scans, `ocr_visible_in_original` records whether plain OCR could read the item in the original.

## Using the kit with your own implementation

**Intake.** The tests in `tests/test_intake.py` call a small interface:

```python
pipe = IntakePipeline(store_root, config_dir, edc_dir)
pipe.import_file(path) -> result     # .status, .records, .errors[{row, column, code}], .outcomes[{key, outcome, eligible, failed_rules, case_id, flags}], .new_cases, .count(outcome)
pipe.manual_entry(record, reason, actor) -> result
pipe.case_for(key) -> case_id | None
pipe.case_state(case_id) -> {"state", "flags"}
pipe.store  # events(case_id), get_json(case_id, rel), list_cases(), evaluations_path, load_index(), rebuild_index(), case_dir(), root
```

Either implement the same interface in your `adjud` package (the tests then run unchanged), or adapt the fixtures in `tests/conftest.py`. The expected results in `expected/intake_expected.json` are implementation-independent.

**De-identification.** Process every file in `documents/` and write outputs to `<out>/<case_key>/<expected_output>`. The output name for each document is in `ground_truth/corpus_index.json`; images come back as PDFs. Then run:

```bash
python tools/deid_check.py --kit . --redacted <out> --ocr --gate --out metrics/my_run.json
```

To see what good and bad look like, run the reference redactors:

```bash
python tools/reference_redactors.py --kit . --mode null   --out out/null     # no redaction: gate fails
python tools/reference_redactors.py --kit . --mode oracle --out out/oracle   # redacts from ground truth: gate passes,
                                                                             # but PHI in images remains (it does no OCR)
```

`metrics/reference_oracle_run.txt` shows the oracle's figures. A real pipeline should match it on text PDFs, spreadsheets and DICOM headers, and beat it on scans, image banners and burned-in pixels.

## Reading the harness results

- **removed / residual** means the item was checked and is gone, or is still present.
- **unverified** means the content is in an image and OCR was off, so removal is not proven. It is never counted as removed.
- **not_measurable** means the harness's own OCR cannot read the item even in the original, so its absence proves nothing. These items are excluded from rates.
- **Text-layer figures are exact; OCR figures are estimates.** OCR can misread an item in the output that it read in the original. Treat small differences on scans as noise, and look at the item list in the JSON output.
- **German OCR.** Install `tesseract-ocr-deu` for meaningful German results. The reference run used English OCR only.

## Decisions the tests encode

The spec left these open. The kit picks one answer so tests can be written; change the expected files if your team decides otherwise.

1. An **uncoded** AE (no PT code) is not eligible (rule R2 fails with "missing value"). An alternative is to flag it for manual review.
2. **Semicolon-delimited** CSV is rejected rather than auto-detected.
3. `serious`, `outcome` and similar coded values are trimmed and case-insensitive.
4. An AE that **stops qualifying** after its case exists is flagged `no_longer_eligible`, never withdrawn automatically.
5. A row with an **older** `last_modified` than stored is ignored as stale. An equal timestamp with different content counts as a change.
6. **Prior events** are strictly before onset, at most 365 days before (365 included), matched on PT, otherwise SOC.
7. A **con med** whose end date equals the onset date counts as ongoing. **Labs** count within ±30 days.
8. **Possible duplicates** are the same subject and PT with overlapping or touching dates.
9. **Staff and facility names** are `should_redact`, so they are reported but not gated. **Next of kin** is `must_redact`.
10. A **PNG with a .pdf extension** is processed as an image with a mismatch warning, not rejected.
11. The **date policy** is `dob_only`: clinical dates are must-keep, dates of birth must-redact (flow1-mvp-spec.md Q-F1-01).

## Regenerating

```bash
python tools/make_synthetic.py --out .
```

The output is deterministic for a given Faker version (seed 20261105). Test TC-COR-12 checks this for the data files.

PDFs embed creation timestamps, so their bytes differ between runs while their content does not. If LibreOffice is not available, the spreadsheet's formula cache is left empty and the formula-value ground-truth item is omitted.
