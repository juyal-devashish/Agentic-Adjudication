"""Write simulated agent answers for the replay demo, before an API key exists (plan
step 15). Synthetic data only.

    uv run python -m tools.simulate_ai            # writes data/config/DEMO01/ai_replays/

Runs the kit's cases and the Flow 2 fixtures through Flows 1 and 2 in a throwaway data
folder with `ai.mode: replay`, collects every agent input that has no recorded answer,
and writes the answer listed in ORACLE below for it. Each file is marked
`"simulated": true`, so the UI shows "Simulated AI", never "AI". The answers are written
by hand from the documents; once the API key arrives (step 22) real outputs replace them.
Inputs without an ORACLE answer are left out: the case then goes to a person.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
REPLAYS = ROOT / "data" / "config" / "DEMO01" / "ai_replays"

# document_type: file name -> (type, page, quote from that page, confidence)
DOCUMENT_TYPES: dict[str, tuple[str, int, str, float]] = {
    "clinic_letter.pdf": ("clinic_letter", 1, "Cardiology Outpatients", 0.9),
    "summary_letter_lowquality.jpg": (
        "discharge_summary",
        1,
        "SUMMARY LETTER TO GENERAL PRACTITIONER",
        0.86,
    ),  # fmt: skip
}
# event_match: (onset date, document names) -> (verdict, [(file, page, quote)], reason, conf)
EVENT_MATCHES: dict[tuple[str, tuple[str, ...]], tuple[str, list, str, float]] = {
    ("2026-03-14", ("discharge_summary.pdf", "ecg_report.pdf")): (
        "different_event",
        [
            ("discharge_summary.pdf", 1, "Fractured neck of femur"),
            ("discharge_summary.pdf", 1, "Admitted on 02/11/2025"),
        ],
        "The documents describe a hip fracture admitted on 2 November 2025, not a "
        "myocardial infarction with onset on 14 March 2026.",
        0.93,
    ),  # fmt: skip
}


def narrative_from_dossier(store, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A plain, mechanical narrative written from the case's own dossier: the EDC facts
    from the summary page, then one statement per document quoting its title line."""
    from adjud.ai.tools import dossier_page_text
    from adjud.qc import Checklist, _is_title

    ae = payload["ae"]
    labels = Checklist.for_study(ROOT / "data" / "config", "DEMO01")
    serious = (
        f"serious ({(ae.get('seriousness_criteria') or '').replace('_', ' ').lower()})"
        if ae.get("serious") == "Y"
        else "not serious"
    )
    statements = [
        {"text": f"The EDC records {ae['ae_term']} with onset on {ae['onset_date']}.",
         "page": 2, "quote": f"Event term: {ae['ae_term']}"},
        {"text": f"The event was reported as {serious}, with the outcome "
                 f"{(ae.get('outcome') or '').replace('_', ' ').lower()}.",
         "page": 2, "quote": f"Outcome: {ae.get('outcome')}"},
    ]  # fmt: skip
    for doc in payload["dossier"]["page_map"]:
        text = dossier_page_text(store, case_id, doc["first_page"]) or ""
        lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 8]
        title = next((ln for ln in lines if _is_title(ln)), lines[0] if lines else "")
        if title:
            statements.append({
                "text": f"The dossier contains the {labels.label(doc['type']).lower()} "
                        f"({doc['name']}) from page {doc['first_page']}.",
                "page": doc["first_page"], "quote": " ".join(title.split()[:12]),
            })  # fmt: skip
    return {"statements": statements}


def oracle(function: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if function == "document_type" and payload["file"] in DOCUMENT_TYPES:
        type_, page, quote, conf = DOCUMENT_TYPES[payload["file"]]
        return {
            "document_type": type_,
            "confidence": conf,
            "evidence": {"file": payload["file"], "page": page, "quote": quote},
        }
    if function == "event_match":
        key = (payload["ae"]["onset_date"], tuple(d["file"] for d in payload["documents"]))
        if key in EVENT_MATCHES:
            verdict, quotes, reason, conf = EVENT_MATCHES[key]
            return {
                "verdict": verdict,
                "confidence": conf,
                "evidence": [{"file": f, "page": p, "quote": q} for f, p, q in quotes],
                "reason": reason,
            }
    return None


def _demo(tmp: Path):
    from adjud.paths import DataPaths
    from adjud.runner import Runner
    from tools.reset_demo import reset

    data = tmp / "data"
    data.mkdir(parents=True)
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    return runner


def _submit_and_check(runner, case_key: str, files: list[Path]) -> str:
    from adjud.documents import documents_of, upload
    from adjud.review import delete_document, submit

    store, pipe = runner.pipeline.store, runner.pipeline
    cid = pipe.case_for(case_key)
    site = f"site{case_key.split('|')[1].split('-')[1]}.coord"
    upload(store, cid, [(f.name, f.read_bytes()) for f in files], site)
    runner.run_case_steps()
    for name, d in documents_of(store, cid).processed.items():
        if d["status"] == "rejected":
            delete_document(store, cid, name, site, reason="rejected file removed")
    submit(store, cid, site, "demo", runner.paths.config)
    return cid


def _run_flow2(runner) -> list[tuple[str, str, dict[str, Any]]]:
    """Run the remaining Flow 2 steps. QC answers are written first, so cases they move on
    reach their narrative too; every narrative input seen is answered from its own case's
    dossier. Earlier uploads also run the runner, so inputs may already be waiting."""
    gw, store = runner.flow2.gateway, runner.pipeline.store
    for _ in range(2):
        runner.run_case_steps()
    qc = [m for m in gw.missing if m[0] != "narrative"]
    _write(qc)
    for _ in range(3):  # cases whose QC answer was just written, then their narrative
        runner.run_case_steps()
    seen, narratives = set(), []
    for function, h, payload in gw.missing:
        if function != "narrative" or h in seen:
            continue
        seen.add(h)
        cid = next(c for c in store.list_cases() if (d := _dossier(store, c)) and
                   d["page_map"] == payload["dossier"]["page_map"])  # fmt: skip
        answer = narrative_from_dossier(store, cid, payload)
        narratives.append((function, h, {**payload, "_answer": answer}))
    gw.missing.clear()
    return qc + narratives


def _dossier(store, case_id: str):
    from adjud.dossier import latest_dossier

    return latest_dossier(store, case_id)


def collect() -> list[tuple[str, str, dict[str, Any]]]:
    """Run every demo case in replay mode; write answers for inputs without one."""
    from tools import make_flow2_fixtures

    os.environ["ADJUD_AI_MODE"] = "replay"
    done = []
    with tempfile.TemporaryDirectory() as tmp:
        runner = _demo(Path(tmp) / "kit")
        for folder in sorted((KIT / "documents").iterdir()):
            subject, ae = folder.name.split("_")
            _submit_and_check(runner, f"DEMO01|{subject}|{ae}", sorted(folder.iterdir()))
        done += _run_flow2(runner)
        fixtures = Path(tmp) / "flow2"
        make_flow2_fixtures.make(fixtures)
        case = json.loads((fixtures / "expected.json").read_text())["case"]
        for scenario in make_flow2_fixtures.EXPECTED:
            runner = _demo(Path(tmp) / scenario)
            _submit_and_check(runner, case, sorted((fixtures / scenario).iterdir()))
            done += _run_flow2(runner)
    _write([d for d in done if d[0] == "narrative"])
    return done


def _write(items: list[tuple[str, str, dict[str, Any]]]) -> None:
    for function, h, payload in items:
        answer = payload.pop("_answer", None) or oracle(function, payload)
        if answer is None:
            continue
        path = REPLAYS / function / f"{h}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"simulated": True, "function": function, "input": payload, "output": answer}
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def main() -> None:
    """Repeat until a pass finds nothing new: an answer written in one pass can move a case
    on to a step that asks the next question (a typed letter lets QC pass, and the case
    then needs its narrative)."""
    for _ in range(4):
        before = len(list(REPLAYS.rglob("*.json")))
        collect()
        if len(list(REPLAYS.rglob("*.json"))) == before:
            break
    print(f"{len(list(REPLAYS.rglob('*.json')))} simulated answers in {REPLAYS}")


if __name__ == "__main__":
    shutil.rmtree(REPLAYS, ignore_errors=True)  # regenerate from scratch
    main()
