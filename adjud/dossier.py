"""The adjudication dossier (plan step 16, QC-04, QC-08).

Built once per passed quality check, from the Flow 2 manifest only:

    page 1   cover: case, subject, event, dossier version, what is inside
    page 2+  EDC summary: the AE, demographics, related data, earlier similar events
    then     the redacted documents in the study's checklist order; spreadsheets are
             printed as text pages, one sheet after another

Every page carries the subject ID and case number in its footer, the PDF has bookmarks,
and `dossier/dossier_v<n>.pdf` is referenced by a `dossier.built` event with its SHA-256
and a page map (which document starts on which page) that the narrative agent cites.
A dossier is never rebuilt in place: a new QC round makes a new version.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pymupdf

from adjud.ai.tools import output_pages
from adjud.events import EventDraft
from adjud.qc import Checklist
from adjud.store import FileCaseStore

SYSTEM = "system:dossier"
A4 = pymupdf.paper_rect("a4")
MARGIN, LINE, WRAP = 56, 14, 95
AE_FIELDS = [
    ("ae_term", "Event term"),
    ("onset_date", "Onset"),
    ("end_date", "End"),
    ("serious", "Serious"),
    ("seriousness_criteria", "Seriousness"),
    ("outcome", "Outcome"),
    ("severity", "Severity"),
    ("relatedness_investigator", "Related to study drug (investigator)"),
    ("pt_code", "Preferred term code"),
    ("soc_code", "System organ class code"),
    ("meddra_version", "Dictionary version"),
    ("last_modified", "Last changed in the EDC"),
]


def _text_pages(doc: pymupdf.Document, title: str, lines: list[str]) -> int:
    """Write `lines` on as many A4 pages as needed; returns the first page number (1-based)."""
    wrapped = [w for ln in lines for w in (textwrap.wrap(ln, WRAP) or [""])]
    per_page = int((A4.height - 2 * MARGIN - 40) // LINE)
    first = doc.page_count + 1
    for start in range(0, max(len(wrapped), 1), per_page):
        page = doc.new_page(width=A4.width, height=A4.height)
        page.insert_text((MARGIN, MARGIN + 6), title, fontsize=15, fontname="helv")
        y = MARGIN + 34
        for ln in wrapped[start : start + per_page]:
            page.insert_text((MARGIN, y), ln, fontsize=9.5, fontname="helv")
            y += LINE
    return first


def _rows(items: list[dict[str, Any]]) -> list[str]:
    return [
        "  "
        + ", ".join(
            f"{k.replace('_', ' ')}: {v}"
            for k, v in it.items()
            if k != "subject_id" and v not in ("", None)
        )  # fmt: skip
        for it in items
    ] or ["  none"]


def build(
    store: FileCaseStore,
    case_id: str,
    manifest: dict[str, Any],
    types: dict[str, dict[str, str]],
    checklist: Checklist,
    version: int,
) -> tuple[bytes, list[dict[str, Any]]]:
    """The dossier PDF and its page map [{name, type, first_page, pages}]."""
    ae = store.get_json(case_id, manifest["ae_snapshot"]["path"])
    related = store.get_json(case_id, manifest["related_data"]["path"])
    prior = store.get_json(case_id, manifest["prior_events"]["path"])
    order = list(checklist.types)
    docs = sorted(
        manifest["documents"],
        key=lambda d: (
            order.index(types.get(d["source_name"], {}).get("type", "other"))
            if types.get(d["source_name"], {}).get("type") in order
            else len(order),
            d["source_name"],
        ),
    )
    subject = manifest["subject"]
    page_map: list[dict[str, Any]] = []
    toc: list[list[Any]] = []
    with pymupdf.open() as doc:
        cover = [
            f"Case {case_id}, study {manifest['study']}",
            f"Subject {subject}",
            f"Event: {ae.get('ae_term', '')}, onset {ae.get('onset_date', '')}",
            f"Dossier version {version}",
            "",
            "Contents",
            "  EDC summary",
            *[
                f"  {d['source_name']} ({
                    checklist.label(types.get(d['source_name'], {}).get('type', 'other'))
                })"
                for d in docs
            ],  # fmt: skip
            "",
            "All documents are de-identified and verified. Synthetic data only.",
        ]
        toc.append([1, "Cover", _text_pages(doc, "Adjudication dossier", cover)])
        demo = related.get("demographics") or {}
        summary = [f"{label}: {ae.get(field) or 'not recorded'}" for field, label in AE_FIELDS]
        summary += ["", "Demographics"]
        summary += [f"  {k.replace('_', ' ')}: {v}" for k, v in demo.items()] or ["  none"]
        for key, items in related.items():
            if key != "demographics" and isinstance(items, list):
                summary += ["", key.replace("_", " ").capitalize(), *_rows(items)]
        summary += ["", "Earlier similar events", *_rows(prior)]
        toc.append([1, "EDC summary", _text_pages(doc, "EDC summary", summary)])
        for d in docs:
            name = d["source_name"]
            type_ = types.get(name, {}).get("type", "other")
            first = doc.page_count + 1
            if d["path"].endswith(".pdf"):
                with pymupdf.open(stream=store.get_file(case_id, d["path"]),
                                  filetype="pdf") as src:  # fmt: skip
                    doc.insert_pdf(src)
            else:  # spreadsheet: print each sheet's cells as text
                lines = []
                for n, sheet in enumerate(output_pages(store, case_id, d["path"]), 1):
                    lines += [f"Sheet {n}", *sheet.splitlines(), ""]
                _text_pages(doc, name, lines)
            pages = doc.page_count - first + 1
            page_map.append({"name": name, "type": type_, "first_page": first, "pages": pages})
            toc.append([1, f"{checklist.label(type_)}: {name}", first])
        total = doc.page_count
        for i, page in enumerate(doc, 1):
            footer = (
                f"Subject {subject}   Case {case_id}   Dossier v{version}   Page {i} of {total}"
            )
            r = page.rect
            page.insert_text((MARGIN, r.height - 24), footer, fontsize=8, fontname="helv",
                             color=(0.25, 0.3, 0.35))  # fmt: skip
        doc.set_toc(toc)
        doc.set_metadata({"title": f"Adjudication dossier {case_id} v{version}"})
        return doc.tobytes(garbage=4, deflate=True), page_map


def build_and_record(
    store: FileCaseStore,
    case_id: str,
    manifest_ref: dict[str, str],
    types: dict[str, dict[str, str]],
    config_dir: Path,
    study: str,
) -> dict[str, Any]:
    version = sum(e["type"] == "dossier.built" for e in store.events(case_id)) + 1
    manifest = store.get_json(case_id, manifest_ref["path"])
    content, page_map = build(store, case_id, manifest, types,
                              Checklist.for_study(config_dir, study), version)  # fmt: skip
    rel = f"dossier/dossier_v{version}.pdf"
    sha = store.put_file(case_id, rel, content)
    data = {"version": version, "dossier": {"path": rel, "sha256": sha},
            "manifest": manifest_ref, "page_map": page_map}  # fmt: skip
    store.append_event(case_id, EventDraft(type="dossier.built", actor=SYSTEM, data=data))
    return data


def latest_dossier(store: FileCaseStore, case_id: str) -> dict[str, Any] | None:
    built = [e["data"] for e in store.events(case_id) if e["type"] == "dossier.built"]
    return built[-1] if built else None
