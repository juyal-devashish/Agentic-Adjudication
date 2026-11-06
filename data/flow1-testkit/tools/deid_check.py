#!/usr/bin/env python3
"""Score de-identified output against the synthetic ground truth.

    python tools/deid_check.py --kit . --redacted out/redacted --ocr --gate --out metrics/run.json

Expected layout of the pipeline's output:  <redacted>/<case_key>/<expected_output>
(see ground_truth/corpus_index.json; images are expected back as PDFs).

For every ground-truth item the harness decides, in the item's channel only:
  must_redact / should_redact  ->  removed | residual | unverified
  must_keep                    ->  preserved | lost | unverified
"unverified" means the content is in an image and OCR was not run, so removal is NOT proven.

Gate (--gate, exit code 1 on failure), matching flow1-mvp-spec.md §15:
  1. every must_redact item in text_pdf documents is removed (none residual, unverified or missing);
  2. no identifier remains in document metadata or properties, in any output;
  3. no output exists for documents that must be rejected.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textmatch import contains  # noqa: E402

META_CHANNELS = {"metadata", "properties"}


# ------------------------------------------------------------------------------------ OCR
def _ocr(img) -> str:
    import pytesseract
    from PIL import Image
    img = img.convert("L")
    if min(img.size) < 1000:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    try:  # orientation detection first, so rotated scans are not silently "clean"
        osd = pytesseract.image_to_osd(img)
        angle = int(next(l.split(":")[1] for l in osd.splitlines() if l.startswith("Rotate")))
        return pytesseract.image_to_string(img.rotate(-angle, expand=True) if angle else img)
    except Exception:
        return "\n".join(pytesseract.image_to_string(img.rotate(a, expand=True)) for a in (0, 90, 180, 270))


def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------------------ extraction
def sniff(path: Path) -> str:
    head = path.read_bytes()[:140]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        return "xlsx"
    if head[128:132] == b"DICM":
        return "dicom"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8"):
        return "image"
    return "unknown"


def channels_pdf(path: Path, ocr: bool) -> dict:
    import pymupdf
    from PIL import Image
    out = {}
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text()
            has_images = bool(page.get_images(full=True))
            verified = True
            if has_images or not text.strip():
                if ocr:
                    pix = page.get_pixmap(dpi=300)
                    text += "\n" + _ocr(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                elif has_images:
                    verified = False
            out[f"page:{i}"] = {"text": text, "verified": verified}
        meta = [str(v) for v in (doc.metadata or {}).values() if v]
        meta.append(doc.get_xml_metadata() or "")
        meta += [str(n) for n in doc.embfile_names()]
        out["metadata"] = {"text": "\n".join(meta), "verified": True}
    return out


def channels_xlsx(path: Path) -> dict:
    from openpyxl import load_workbook
    wb_f = load_workbook(path)
    wb_v = load_workbook(path, data_only=True)
    ch = {k: [] for k in ("cells", "hidden", "comments", "properties", "defined_names")}
    for ws in wb_f.worksheets:
        wv = wb_v[ws.title]
        sheet_hidden = ws.sheet_state != "visible"
        for row in ws.iter_rows():
            for c in row:
                vals = [c.value, wv[c.coordinate].value]
                if not any(v not in (None, "") for v in vals):
                    continue
                hidden = (sheet_hidden or ws.column_dimensions[c.column_letter].hidden
                          or ws.row_dimensions[c.row].hidden)
                ch["hidden" if hidden else "cells"] += [str(v) for v in vals if v not in (None, "")]
                if c.comment:
                    ch["comments"].append(c.comment.text)
    p = wb_f.properties
    ch["properties"] = [str(getattr(p, a)) for a in ("creator", "title", "subject", "description", "keywords",
                                                     "lastModifiedBy", "category") if getattr(p, a, None)]
    ch["defined_names"] = [f"{n}={d.attr_text}" for n, d in wb_f.defined_names.items()]
    return {k: {"text": "\n".join(v), "verified": True} for k, v in ch.items()}


def channels_dicom(path: Path, ocr: bool) -> dict:
    import numpy as np
    import pydicom
    from PIL import Image
    ds = pydicom.dcmread(path)
    vals = []
    for elem in list(ds.file_meta.iterall()) + list(ds.iterall()):
        if elem.tag == (0x7FE0, 0x0010) or elem.VR in ("OB", "OW", "OF", "UN", "SQ"):
            continue
        vals.append(str(elem.value))
    out = {"dicom_header": {"text": "\n".join(vals), "verified": True}}
    if "PixelData" in ds:
        if ocr:
            a = ds.pixel_array.astype(float)
            img = Image.fromarray((255 * (a - a.min()) / max(np.ptp(a), 1)).astype("uint8"))
            out["pixels"] = {"text": _ocr(img), "verified": True}
        else:
            out["pixels"] = {"text": "", "verified": False}
    return out


def channels_image(path: Path, ocr: bool) -> dict:
    from PIL import Image
    if not ocr:
        return {"page:1": {"text": "", "verified": False}}
    return {"page:1": {"text": _ocr(Image.open(path)), "verified": True}}


def extract(path: Path, ocr: bool) -> dict:
    kind = sniff(path)
    return {"pdf": lambda: channels_pdf(path, ocr), "xlsx": lambda: channels_xlsx(path),
            "dicom": lambda: channels_dicom(path, ocr), "image": lambda: channels_image(path, ocr)}.get(
        kind, lambda: {})()


# ------------------------------------------------------------------------------------ scoring
def score(kit: Path, redacted: Path, ocr: bool, only: set[str] | None = None) -> dict:
    index = json.loads((kit / "ground_truth" / "corpus_index.json").read_text(encoding="utf-8"))
    docs_out, items_out = [], []
    for d in index["documents"]:
        if only and d["doc_id"] not in only:
            continue
        case_dir = redacted / d["case_key"]
        if d["expected_handling"] != "process":
            leaked = case_dir.exists() and any(p.name.startswith(Path(d["file"]).stem) for p in case_dir.iterdir())
            docs_out.append({"doc_id": d["doc_id"], "variant": d["variant"], "status": "OUTPUT_SHOULD_NOT_EXIST" if leaked else "rejected_ok"})
            continue
        gt = json.loads((kit / d["ground_truth"]).read_text(encoding="utf-8"))
        out_path = case_dir / d["expected_output"]
        if not out_path.exists():
            docs_out.append({"doc_id": d["doc_id"], "variant": d["variant"], "status": "MISSING"})
            for it in gt["items"]:
                items_out.append({**it, "doc_id": d["doc_id"], "variant": d["variant"], "status": "missing"})
            continue
        ch = extract(out_path, ocr)
        orig = extract(kit / d["file"], ocr)   # baseline: can this item be seen in the ORIGINAL at all?
        meta_hits = []
        for it in gt["items"]:
            c = ch.get(it["channel"], {"text": "", "verified": True})
            o = orig.get(it["channel"], {"text": "", "verified": False})
            found = contains(c["text"], it["value"])
            if o["verified"] and not contains(o["text"], it["value"]):
                status = "not_measurable"          # e.g. OCR cannot read it even in the original
            elif not o["verified"]:
                status = "unverified"
            elif it["requirement"] == "must_keep":
                status = "preserved" if found else ("lost" if c["verified"] else "unverified")
            else:
                status = "residual" if found else ("removed" if c["verified"] else "unverified")
            if found and it["channel"] in META_CHANNELS and it["requirement"] != "must_keep":
                meta_hits.append(it["id"])
            items_out.append({"doc_id": d["doc_id"], "variant": d["variant"], "id": it["id"], "value": it["value"],
                              "category": it["category"], "requirement": it["requirement"], "channel": it["channel"],
                              "locations": it["locations"], "status": status})
        unverified = [k for k, v in ch.items() if not v["verified"]]
        docs_out.append({"doc_id": d["doc_id"], "variant": d["variant"], "status": "scored",
                         "unverified_channels": unverified, "metadata_identifier_hits": meta_hits})
    return {"ocr": ocr, "documents": docs_out, "items": items_out, "summary": summarise(docs_out, items_out)}


def _rate(c: dict, good: str, bad: tuple) -> float | None:
    total = c.get(good, 0) + sum(c.get(b, 0) for b in bad)
    return round(c.get(good, 0) / total, 4) if total else None


def summarise(docs, items) -> dict:
    by = defaultdict(lambda: defaultdict(int))
    for it in items:
        for k in (f"variant={it['variant']}|{it['requirement']}", f"category={it['category']}|{it['requirement']}",
                  f"all|{it['requirement']}"):
            by[k][it["status"]] += 1
    table = {}
    for k, c in sorted(by.items()):
        req = k.split("|")[1]
        table[k] = {**c, "rate": _rate(c, "preserved", ("lost", "unverified", "missing")) if req == "must_keep"
                    else _rate(c, "removed", ("residual", "unverified", "missing"))}
    text_pdf_must = [i for i in items if i["variant"] == "text_pdf" and i["requirement"] == "must_redact"]
    gate = {
        "text_pdf_must_redact_all_removed": bool(text_pdf_must) and all(i["status"] == "removed" for i in text_pdf_must),
        "no_identifiers_in_metadata": all(not d.get("metadata_identifier_hits") for d in docs)
                                      and not any(i["channel"] in META_CHANNELS and i["requirement"] != "must_keep"
                                                  and i["status"] in ("residual", "missing") for i in items),
        "rejected_documents_have_no_output": all(d["status"] != "OUTPUT_SHOULD_NOT_EXIST" for d in docs),
    }
    gate["passed"] = all(gate.values())
    return {"rates": table, "gate": gate}


def print_report(res: dict):
    print(f"OCR: {'on' if res['ocr'] else 'OFF (image content cannot be verified)'}")
    print(f"{'group':58s} {'rate':>7s}  counts")
    for k, v in res["summary"]["rates"].items():
        counts = ", ".join(f"{s}={n}" for s, n in v.items() if s != "rate")
        rate = "-" if v["rate"] is None else f"{v['rate']:.0%}"
        print(f"{k:58s} {rate:>7s}  {counts}")
    for d in res["documents"]:
        if d["status"] not in ("scored", "rejected_ok") or d.get("metadata_identifier_hits"):
            print(f"! {d['doc_id']} {d['variant']}: {d['status']} {d.get('metadata_identifier_hits') or ''}")
    g = res["summary"]["gate"]
    print("GATE:", "PASSED" if g["passed"] else "FAILED", {k: v for k, v in g.items() if k != "passed"})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kit", default=".", help="test kit root (contains ground_truth/)")
    ap.add_argument("--redacted", required=True, help="pipeline output root")
    ap.add_argument("--ocr", action="store_true", help="OCR image content (needs Tesseract)")
    ap.add_argument("--out", help="write full JSON results here")
    ap.add_argument("--gate", action="store_true", help="exit 1 if the gate fails")
    ap.add_argument("--only", nargs="*", help="score only these doc IDs (e.g. D04 D09)")
    a = ap.parse_args()
    if a.ocr and not ocr_available():
        sys.exit("Tesseract not available; install it or run without --ocr")
    res = score(Path(a.kit), Path(a.redacted), a.ocr, set(a.only) if a.only else None)
    print_report(res)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    if a.gate and not res["summary"]["gate"]["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
