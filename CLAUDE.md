# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

AGENTS.md (imported above) holds the shared rules for all agents: stack, git workflow, correctness rules, testing, AI-call limits and commands. This file adds only the big picture you need before touching code.

## Repository state

Check the plan's ticked boxes for progress. `adjud/` is our implementation; `data/flow1-testkit/adjud/` is the kit's *reference* intake code, kept only so the kit is self-contained. Don't import from it or edit it.

## Which document governs what

The project builds **Flows 1 and 2**. Flow 1 is done; Flow 2 and the AI layer are being built.

| Document | Use it for |
|---|---|
| [docs/plan-mvp.md](docs/plan-mvp.md) | What to build next: step order, the autonomy model (§2), decisions taken (P-01 onwards), done criteria. Wins over the specs where they differ. Check which boxes are ticked before starting work |
| Code, `tests/` and [data/flow1-testkit/](data/flow1-testkit/README.md) | Flow 1 behavior (its spec was retired after the demo; see git history before `docs/plan-mvp.md`). The kit holds the acceptance tests, the synthetic corpus with ground truth and the scoring harness |
| [docs/mvp-spec.md](docs/mvp-spec.md) | Storage design (§4), Flow 2 states (§5.2), requirements MVP-11 to MVP-15 (§8), AI rules (§9). Flow 3–4 content doesn't apply |
| [docs/spec.md](docs/spec.md) | Flow 2 requirements (§6.3 QC-, §6.4 NAR-), background and requirement IDs. Don't build its production stack |
| [docs/ui-design-plan.md](docs/ui-design-plan.md) | The UI's visual system and copy rules |

## Architecture

**Event-sourced case folders, no database.** Every case is `data/cases/<id>/`. Its only source of truth is a hash-chained, append-only `events.jsonl`; `case.json` is a cache rebuilt by replaying events through the workflow table. Artifacts (original, working and redacted documents, reports, source snapshots, the handoff manifest) are separate files, referenced from events by relative path + SHA-256. Records that never become cases go to a separate hash-chained log, `data/intake/evaluations.jsonl`, so every "not eligible" result stays auditable. The rebuild and verify tools must cover both logs.

**Two writers, one interface.** The Streamlit UI (`ui/`) appends human actions such as uploads, corrections and signatures. The single `runner` loop (`adjud/runner.py`) imports from `inbox/` and runs every case sitting in an automatic state (`EVENT_CREATED`, `DEIDENTIFYING`, handoff after `SUBMITTED`, and the Flow 2 agent steps as they are built). Both go through `CaseStore` (`adjud/store.py`), which locks, validates the transition, chains the hash and appends. The case's state *is* the queue; there is no job system. `adjud/workflow.py` owns the transition table and `replay()`.

**De-identification pipeline:** store original → validate by content → normalise (images to PDF pages) → extract text from **both** the text layer and 300 dpi OCR of every page → detect (Presidio, custom regex, label-context rules, optional known values, optional AI-1) → apply `deid_policy.yaml` (allow-list, date policy, default `dob_only`) → destructive redaction (text pages: PyMuPDF `apply_redactions`; scanned pages: flatten into an image-only page) → `scrub()` metadata → **verify from scratch** on the output file → render previews. Any residual finding blocks submission.

**Boundary between flows.** Flow 1 ends by writing `handoff/flow2_manifest.json`: redacted files, source snapshots and their hashes, and never anything from `documents/original/`. Flow 2 reads only that manifest. Keep that contract stable. Flow 2 ends the same way with `handoff/flow3_manifest.json`.

**AI layer (plan §2, step 12).** Agents run only through `adjud/ai/gateway.py` (modes `off` / `replay` / `live`) and act by calling local tools in `adjud/ai/tools.py`. `adjud/ai/gates.py` decides whether an agent result may move the case; if not, a person gets an exception task. Every call is logged per case and in a cost ledger.

**Measurement is a deliverable.** The kit's `tools/make_synthetic.py` writes seeded fake PHI plus a ground-truth file. `tools/deid_check.py` scores recall, over-redaction, metadata residuals and time per page into `data/metrics/`. Text-layer PDFs must reach 100% removal. Results for scans, spreadsheets and DICOM are reported, not gated, so they can't be tuned toward a target.

**Config drives study behavior.** Everything study-specific lives in `data/config/<STUDY>/`. Eligibility rules are interpreted declaratively from YAML; other files cover which related EDC data to pull, the de-identification policy, AI switches and form definitions.
