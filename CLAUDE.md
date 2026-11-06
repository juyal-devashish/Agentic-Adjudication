# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

AGENTS.md (imported above) holds the shared rules for all agents: stack, git workflow, correctness rules, testing, AI-call limits and commands. This file adds only the big picture you need before touching code.

## Repository state

Check the plan's ticked boxes for progress. `adjud/` is our implementation; `data/flow1-testkit/adjud/` is the kit's *reference* intake code, kept only so the kit is self-contained. Don't import from it or edit it.

## Which document governs what

The project builds **Flow 1 only**.

| Document | Use it for |
|---|---|
| [docs/plan-mvp-flow-one.md](docs/plan-mvp-flow-one.md) | What to build next: step order, core vs stretch scope, decisions taken, done criteria. Check which boxes are ticked before starting work |
| [docs/mvp-flow-1.md](docs/mvp-flow-1.md) | Required behavior; wins over the other specs where they differ |
| [docs/flow-1-testcases.md](docs/flow-1-testcases.md) + [data/flow1-testkit/](data/flow1-testkit/README.md) | Acceptance tests (TC-RUL, TC-INT, TC-DOC, TC-COR, TC-HAR), the synthetic corpus with ground truth, and the scoring harness. Where the kit encodes a decision the spec left open, the kit wins (plan §4, P-12 to P-20) |
| [docs/mvp-spec.md](docs/mvp-spec.md) | Shared storage design (§4) only. Its Flow 2–4 content and its own states and build order don't apply |
| [docs/spec.md](docs/spec.md) | Background and requirement IDs (INT-, DOC-, AI-G…). Don't build its production stack |

## Architecture (as specified)

**Event-sourced case folders, no database.** Every case is `data/cases/<id>/`. Its only source of truth is a hash-chained, append-only `events.jsonl`; `case.json` is a cache rebuilt by replaying events through the workflow table. Artifacts (original, working and redacted documents, reports, source snapshots, the handoff manifest) are separate files, referenced from events by relative path + SHA-256. Records that never become cases go to a separate hash-chained log, `data/intake/evaluations.jsonl`, so every "not eligible" result stays auditable. The rebuild and verify tools must cover both logs.

**Two writers, one interface.** The Streamlit UI (`ui/`) appends human actions such as uploads, corrections and signatures. The single `runner` loop (`adjud/runner.py`) imports from `inbox/` and runs every case sitting in an automatic state (`EVENT_CREATED`, `DEIDENTIFYING`, and handoff after `SUBMITTED`). Both go through `CaseStore` (`adjud/store.py`), which locks, validates the transition, chains the hash and appends. The case's state *is* the queue; there is no job system. `adjud/workflow.py` owns the transition table and `replay()`.

**De-identification pipeline** (mvp-flow-1.md §7.2): store original → validate by content → normalise (images to PDF pages) → extract text from **both** the text layer and 300 dpi OCR of every page → detect (Presidio, custom regex, label-context rules, optional known values, optional AI-1) → apply `deid_policy.yaml` (allow-list, date policy, default `dob_only`) → destructive redaction (text pages: PyMuPDF `apply_redactions`; scanned pages: flatten into an image-only page) → `scrub()` metadata → **verify from scratch** on the output file → render previews. Any residual finding blocks submission.

**Boundary between flows.** Flow 1 ends by writing `handoff/flow2_manifest.json`: redacted files, source snapshots and their hashes, and never anything from `documents/original/`. Flow 2 reads only that manifest. Keep that contract stable.

**Measurement is a deliverable.** `tools/make_synthetic.py` writes seeded fake PHI plus a ground-truth file. `tools/deid_check.py` scores recall, over-redaction, metadata residuals and time per page into `data/metrics/`. Text-layer PDFs must reach 100% removal. Results for scans, spreadsheets and DICOM are reported, not gated, so they can't be tuned toward a target.

**Config drives study behavior.** Everything study-specific lives in `data/config/<STUDY>/`. Eligibility rules are interpreted declaratively from YAML; other files cover which related EDC data to pull, the de-identification policy, AI switches and form definitions.
