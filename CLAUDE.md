# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

AGENTS.md (imported above) holds the shared rules for all agents: stack, git workflow, correctness rules, testing, AI-call limits and commands. This file adds only the big picture you need before touching code.

## Repository state

The repository holds specs only; no code exists yet. Commands in AGENTS.md are the ones the specs define. Once `pyproject.toml`, `docker-compose.yml` or a test config exists, trust those over the specs and update AGENTS.md to match.

## Which spec governs what

| Spec | Covers | Use it for |
|---|---|---|
| [docs/mvp-flow-1.md](docs/mvp-flow-1.md) | Flow 1 (intake + de-identification) in depth, ending at a hashed handoff to Flow 2 | Any intake, de-identification or site-review work |
| [docs/mvp-spec.md](docs/mvp-spec.md) | The whole MVP (Flows 1–4), thinner on Flow 1 | Storage design (§4), workflow states (§5), QC, narrative, adjudication, outcome |
| [docs/spec.md](docs/spec.md) | Full validated product; source of requirement IDs (INT-, DOC-, ADJ-, AI-G…) | Background and intent only. Don't build its production stack |

The Flow 1 spec is more detailed than mvp-spec.md, and they **differ**. Ask the user which applies before building any of these:
- **AI-1 (AI PHI detection):** excluded by mvp-spec.md §9.1 and AGENTS.md, but a switchable "Could" in mvp-flow-1.md §8 (synthetic data only).
- **File types:** mvp-spec.md defers XLSX and DICOM; mvp-flow-1.md has XLSX and DICOM header as Should and adds TIFF.
- **States:** mvp-flow-1.md adds `EVENT_CREATED` and ends at `SUBMITTED`, and has `deid.failed` and `source.changed` events. mvp-spec.md goes straight from case creation to `AWAITING_DOCUMENTS` and continues to `QC_CHECKS`.
- **Build order:** each spec has its own §14 or §16. Both start with the same skeleton step.

## Architecture (as specified)

**Event-sourced case folders, no database.** Every case is `data/cases/<id>/`. Its only source of truth is a hash-chained, append-only `events.jsonl`; `case.json` is a cache rebuilt by replaying events through the workflow table. Artifacts (documents, reports, forms, dossiers, outcomes) are separate files, referenced from events by relative path + SHA-256. Records that never become cases go to a separate hash-chained log, `data/intake/evaluations.jsonl`, so every "not eligible" result stays auditable. The rebuild and verify tools must cover both logs.

**Two writers, one interface.** The Streamlit UI (`ui/`) appends human actions such as uploads, corrections and signatures. The single `runner` loop (`adjud/runner.py`) imports from `inbox/` and runs every case sitting in an automatic state, such as de-identification, QC checks, comparison and outcome. Both go through `CaseStore` (`adjud/store.py`), which locks, validates the transition, chains the hash and appends. The case's state *is* the queue; there is no job system. `adjud/workflow.py` owns the transition table and `replay()`.

**De-identification pipeline** (mvp-flow-1.md §7.2): store original → validate by content → normalise (images to PDF pages) → extract text from **both** the text layer and 300 dpi OCR of every page → detect (Presidio, custom regex, label-context rules, optional known values, optional AI-1) → apply `deid_policy.yaml` (allow-list, date policy, default `dob_only`) → destructive redaction (text pages: PyMuPDF `apply_redactions`; scanned pages: flatten into an image-only page) → `scrub()` metadata → **verify from scratch** on the output file → render previews. Any residual finding blocks submission.

**Boundary between flows.** Flow 1 ends by writing `handoff/flow2_manifest.json`: redacted files, source snapshots and their hashes, and never anything from `documents/original/`. Flow 2 reads only that manifest. Keep that contract stable.

**Measurement is a deliverable.** `tools/make_synthetic.py` writes seeded fake PHI plus a ground-truth file. `tools/deid_check.py` scores recall, over-redaction, metadata residuals and time per page into `data/metrics/`. Text-layer PDFs must reach 100% removal. Results for scans, spreadsheets and DICOM are reported, not gated, so they can't be tuned toward a target.

**Config drives study behavior.** Everything study-specific lives in `data/config/<STUDY>/`. Eligibility rules are interpreted declaratively from YAML; other files cover which related EDC data to pull, the de-identification policy, AI switches and form definitions.
