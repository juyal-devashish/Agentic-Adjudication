"""Page 3 — Results (mvp-flow-1.md §9.3): case list, timeline with chain check, source
data, notifications. Handoff and metrics arrive in steps 4–6."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pandas as pd
import streamlit as st
from common import context, paths, pipeline

from adjud.handoff import read_manifest
from adjud.integrity import verify_case_log

ctx = context()
pipe = pipeline()
store = pipe.store

st.title("3 · Results")

states = store.list_case_states()
st.subheader(f"Cases ({len(states)})")
if states:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "case": cs.case_id,
                    "subject": cs.subject,
                    "AE": cs.ae_id,
                    "state": cs.state.value,
                    "since": cs.state_since,
                    "flags": ", ".join(cs.flags),
                }
                for cs in states
            ]
        ),
        hide_index=True,
        width="stretch",
    )

if ctx.case_id is None:
    st.info("Pick a case in the sidebar to see its timeline.")
    st.stop()

case_dir = store.case_dir(ctx.case_id)
report = verify_case_log(case_dir)
st.subheader(f"Timeline — {ctx.case_id}")
if report.ok:
    st.success(f"Chain verified ✓ ({report.records} events, every referenced file matches)")
else:
    st.error("Chain verification FAILED:\n\n" + "\n".join(f"- {p}" for p in report.problems))

events = store.events(ctx.case_id)
st.dataframe(
    pd.DataFrame(
        [
            {
                "#": e["seq"],
                "at": e["at"],
                "actor": e["actor"],
                "event": e["type"],
                "reason": e.get("reason") or "",
                "hash": e["hash"][7:19],
            }
            for e in events
        ]
    ),
    hide_index=True,
    width="stretch",
)

st.subheader("Source data")
tabs = st.tabs(["AE snapshot", "Eligibility", "Related data", "Prior events"])
with tabs[0]:
    st.json(pipe.latest_snapshot(ctx.case_id))
with tabs[1]:
    st.json(store.get_json(ctx.case_id, "source/eligibility_v1.json"), expanded=1)
with tabs[2]:
    st.json(store.get_json(ctx.case_id, "source/related_data.json"))
with tabs[3]:
    st.json(store.get_json(ctx.case_id, "source/prior_events.json"))

st.subheader("Notifications")
notes = [json.loads(f.read_text()) for f in sorted(paths().outbox.glob("*.json"))]
mine = [n for n in notes if n.get("case_id") == ctx.case_id]
if mine:
    st.dataframe(
        pd.DataFrame(
            [{k: n[k] for k in ("created_at", "to", "template", "subject")} for n in mine]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("None.")

# --- handoff to Flow 2 -------------------------------------------------------------------------

st.subheader("Handoff to Flow 2")
manifest = read_manifest(store, ctx.case_id)
if manifest is None:
    st.write("Not submitted yet.")
else:
    s = manifest["submission"]
    st.success(f"Signed by {s['signer']} at {s['signed_at']} — *{s['meaning']}*")
    for d in manifest["documents"]:
        data = store.get_file(ctx.case_id, d["path"])
        st.download_button(
            f"Download {d['path'].rsplit('/', 1)[-1]} (redacted, {d['pages'] or '?'} page(s))",
            data,
            file_name=d["path"].rsplit("/", 1)[-1],
            key=d["path"],
        )
    with st.expander("flow2_manifest.json"):
        st.json(manifest)

# --- de-identification metrics -----------------------------------------------------------------

st.subheader("De-identification metrics (synthetic test corpus)")
latest = paths().metrics / "latest.json"
if ctx.role == "safety_specialist" and st.button("Run measurement now (≈30 s, OCR)"):
    with st.spinner("Running the pipeline over the kit corpus and scoring it…"):
        subprocess.run(
            [sys.executable, "-m", "tools.deid_check"],
            check=False,  # noqa: S603
            capture_output=True,
        )
    st.rerun()
if not latest.exists():
    st.write("No measurement yet. Run `uv run python -m tools.deid_check`.")
else:
    res = json.loads(latest.read_text())
    gate = res["summary"]["gate"]
    (st.success if gate["passed"] else st.error)(
        f"Gate {'PASSED' if gate['passed'] else 'FAILED'} — "
        + ", ".join(
            f"{k.replace('_', ' ')}: {'yes' if v else 'NO'}"
            for k, v in gate.items()
            if k != "passed"
        )
    )
    oracle = {}
    kit_ref = paths().root / "flow1-testkit" / "metrics" / "reference_oracle_run.txt"
    if kit_ref.exists():
        for line in kit_ref.read_text().splitlines():
            m = re.match(r"^(variant=\S+)\s+(\d+)%", line)
            if m:
                oracle[m.group(1)] = f"{m.group(2)}%"
    rows = []
    for key, v in res["summary"]["rates"].items():
        if not key.startswith("variant="):
            continue
        variant, req = key.removeprefix("variant=").split("|")
        rows.append(
            {
                "document variant": variant,
                "requirement": req,
                "rate": None if v["rate"] is None else f"{v['rate']:.0%}",
                "oracle (no OCR)": oracle.get(key, ""),
                "counts": ", ".join(f"{k}={n}" for k, n in v.items() if k != "rate"),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        f"OCR {'on' if res['ocr'] else 'off'}; pipeline time {res['run']['seconds']} s. "
        "Synthetic data only. Rates on scans are OCR estimates (kit README)."
    )
