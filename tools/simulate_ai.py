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


def _submit_and_check(runner, case_key: str, files: list[Path]) -> None:
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
    for _ in range(2):  # handoff + QC start, then the QC round with its agents
        runner.run_case_steps()


def collect() -> list[tuple[str, str, dict[str, Any]]]:
    """Every agent input of the demo cases that has no recorded answer yet."""
    from tools import make_flow2_fixtures

    os.environ["ADJUD_AI_MODE"] = "replay"
    missing = []
    with tempfile.TemporaryDirectory() as tmp:
        runner = _demo(Path(tmp) / "kit")
        for folder in sorted((KIT / "documents").iterdir()):
            subject, ae = folder.name.split("_")
            _submit_and_check(runner, f"DEMO01|{subject}|{ae}", sorted(folder.iterdir()))
        missing += runner.flow2.gateway.missing
        fixtures = Path(tmp) / "flow2"
        make_flow2_fixtures.make(fixtures)
        case = json.loads((fixtures / "expected.json").read_text())["case"]
        for scenario in make_flow2_fixtures.EXPECTED:
            runner = _demo(Path(tmp) / scenario)
            _submit_and_check(runner, case, sorted((fixtures / scenario).iterdir()))
            missing += runner.flow2.gateway.missing
    return missing


def main() -> None:
    written = skipped = 0
    for function, h, payload in collect():
        answer = oracle(function, payload)
        if answer is None:
            skipped += 1
            continue
        path = REPLAYS / function / f"{h}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"simulated": True, "function": function, "input": payload, "output": answer}
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        written += 1
    print(f"{written} simulated answers in {REPLAYS}; {skipped} inputs left to a person")


if __name__ == "__main__":
    shutil.rmtree(REPLAYS, ignore_errors=True)  # regenerate from scratch
    main()
