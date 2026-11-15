"""Screenshots of every page for design review (docs/ui-design-plan.md).

    uv run python -m tools.screenshots --out /tmp/shots

Prepares a throw-away demo (reset, import, one case in site review, one submitted) in a
temporary data dir, starts Streamlit on it, and captures each page at desktop and phone
width with Playwright. Synthetic data only.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
PORT = 8597


def prepare(data: Path) -> dict[str, str]:
    from adjud.documents import upload
    from adjud.paths import DataPaths
    from adjud.review import submit
    from adjud.runner import Runner
    from tools.reset_demo import reset

    data.mkdir(parents=True)
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    if (ROOT / "data" / "metrics" / "latest.json").exists():
        (data / "metrics").mkdir()
        (data / "metrics" / "latest.json").write_bytes(
            (ROOT / "data" / "metrics" / "latest.json").read_bytes()
        )
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    pipe, docs = runner.pipeline, KIT / "documents"
    review = pipe.case_for("DEMO01|DEMO01-104-0006|AE-007")
    folder = docs / "DEMO01-104-0006_AE-007"
    upload(
        pipe.store,
        review,
        [
            (n, (folder / n).read_bytes())
            for n in ("entlassungsbrief.pdf", "roentgen_thorax.pdf", "notizen.txt")
        ],
        "site104.coord",
    )
    done = pipe.case_for("DEMO01|DEMO01-101-0001|AE-001")
    folder = docs / "DEMO01-101-0001_AE-001"
    upload(
        pipe.store,
        done,
        [
            (n, (folder / n).read_bytes())
            for n in ("discharge_summary.pdf", "ecg_report.pdf", "troponin_results.xlsx")
        ],  # fmt: skip
        "site101.coord",
    )
    runner.run_case_steps()
    submit(pipe.store, done, "site101.coord", "demo", paths.config)
    from adjud.flow2 import Flow2

    flow2 = Flow2(pipe.store, paths.config)
    for _ in range(2):  # Flow 2: handoff, QC round
        runner.run_case_steps()
    flow2.sign_qc_pass(done, "ss.anna", "demo")  # the Safety Specialist's two sign-offs (P-37)
    runner.run_case_steps()  # dossier and narrative draft
    draft = next(e for e in pipe.store.events(done) if e["type"] == "narrative.drafted")
    text = pipe.store.get_file(done, draft["data"]["draft"]["path"]).decode()
    flow2.sign_narrative(done, "ss.anna", "demo", text)
    runner.run_case_steps()  # handoff to Flow 3

    # The Safety Specialist's decision screens and a site's open query: documents without
    # a subject ID, so no check or recorded agent answer can decide and a person must.
    import pymupdf

    def to_qc_review(key: str, site_user: str) -> str:
        cid = pipe.case_for(key)
        files = []
        for name, text in (
            ("summary.pdf", "DISCHARGE SUMMARY. Chest pain."),
            ("ecg.pdf", "ECG REPORT. Sinus rhythm."),
        ):
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72, 72), text)
                files.append((name, doc.tobytes()))
        upload(pipe.store, cid, files, site_user)
        runner.run_case_steps()
        submit(pipe.store, cid, site_user, "demo", paths.config)
        for _ in range(3):  # handoff, QC start, QC round
            runner.run_case_steps()
        return cid

    qc_review = to_qc_review("DEMO01|DEMO01-101-0002|AE-002", "site101.coord")
    query = to_qc_review("DEMO01|DEMO01-102-0003|AE-003", "site102.coord")
    flow2.open_query(query, ["missing_document"], "Please upload the ECG report.",
                     user_id="ss.anna", password="demo")  # noqa: S106  # fmt: skip
    narrative = to_qc_review("DEMO01|DEMO01-101-0005|AE-006", "site101.coord")
    flow2.sign_qc_pass(narrative, "ss.anna", "demo", "")
    runner.run_case_steps()  # template draft: no recorded answer for these documents
    return {"review": review, "done": done, "qc_review": qc_review, "query": query,
            "narrative": narrative}  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp()) / "data"
    os.environ["ADJUD_AI_MODE"] = "replay"  # Flow 2 agents answer from the recorded replays
    cases = prepare(data)
    env = {**os.environ, "ADJUD_DATA_DIR": str(data)}
    server = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "ui/app.py",
            "--server.headless=true",
            f"--server.port={PORT}",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://localhost:{PORT}/_stcore/health")  # noqa: S310
                break
            except OSError:
                time.sleep(0.5)
        from playwright.sync_api import sync_playwright

        shots = [
            ("1_import", "?role=data_manager"),  # the default page lives at /
            (
                "2_documents_review",
                f"documents?role=site&user=site104.coord&case={cases['review']}",
            ),
            ("3_documents_submitted", f"documents?role=site&case={cases['done']}"),
            ("4_record", f"record?role=safety_specialist&case={cases['done']}"),
            ("6_quality_check", f"qc?role=safety_specialist&case={cases['done']}"),
            ("5_record_list", "record?role=safety_specialist"),
            ("7_qc_review", f"qc?role=safety_specialist&case={cases['qc_review']}"),
            ("8_record_needs_qc", f"record?role=safety_specialist&case={cases['qc_review']}"),
            ("9_narrative_review", f"qc?role=safety_specialist&case={cases['narrative']}"),
            ("10_site_query", f"documents?role=site&case={cases['query']}"),
            ("11_documents_reader", f"documents?role=safety_specialist&case={cases['review']}"),
            ("12_documents_no_case", "documents?role=site&user=site101.coord"),
        ]
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, suffix in ((1440, "desktop"), (390, "phone")):
                page = browser.new_page(viewport={"width": width, "height": 2400})
                for name, path in shots:
                    page.goto(f"http://localhost:{PORT}/{path}")
                    page.wait_for_selector("[data-testid='stMainBlockContainer']")
                    page.wait_for_timeout(3500)
                    page.screenshot(path=args.out / f"{name}_{suffix}.png", full_page=True)
                page.close()
            browser.close()
    finally:
        server.terminate()
    print(f"screenshots in {args.out}")


if __name__ == "__main__":
    main()
