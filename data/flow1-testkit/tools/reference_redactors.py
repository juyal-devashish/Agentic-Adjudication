#!/usr/bin/env python3
"""Two reference "pipelines" used to test the harness itself (and as a starting example).

  null    Converts every accepted file to the expected output format WITHOUT redacting anything.
          The harness must report residual PHI and fail the gate.
  oracle  Redacts using the ground-truth values (cheating on purpose). Handles text-layer PDFs,
          spreadsheets and DICOM headers properly; it does NOT redact text inside images.
          The harness must pass the gate, and must still report residual PHI in scans,
          image banners and burned-in pixels, showing exactly where a real pipeline needs OCR.

Both apply the same intake checks a real pipeline needs: type from content, encrypted and
corrupt PDFs rejected, unsupported types rejected.

    python tools/reference_redactors.py --kit . --mode oracle --out out/oracle
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deid_check import sniff  # noqa: E402


class Rejected(Exception):
    pass


def open_pdf(path: Path):
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise Rejected("CORRUPT") from e
    if doc.needs_pass:
        raise Rejected("ENCRYPTED")
    if doc.page_count == 0:
        raise Rejected("CORRUPT")
    return doc


def image_to_pdf(src: Path, dst: Path):
    from PIL import Image
    img = Image.open(src)
    dpi = img.info.get("dpi", (300, 300))[0] or 300
    buf = io.BytesIO(); img.convert("L").save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=img.width * 72 / dpi, height=img.height * 72 / dpi)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(dst, garbage=4, deflate=True)


def redact_pdf(src: Path, dst: Path, values: list[str]):
    doc = open_pdf(src)
    for page in doc:
        hit = False
        for v in values:
            for r in page.search_for(v):
                page.add_redact_annot(r, fill=(0, 0, 0)); hit = True
        if hit:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
    doc.scrub()
    doc.set_metadata({})
    doc.save(dst, garbage=4, deflate=True)


def redact_xlsx(src: Path, dst: Path, values: list[str]):
    from openpyxl import load_workbook
    from textmatch import contains
    wb, cached = load_workbook(src), load_workbook(src, data_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and v.startswith("="):
                    v = c.value = cached[ws.title][c.coordinate].value   # formulas -> values
                if v is not None and any(contains(str(v), x) for x in values):
                    c.value = "[REDACTED]"
                c.comment = None
    for name in list(wb.defined_names.keys()):
        del wb.defined_names[name]
    for a in ("creator", "title", "subject", "description", "keywords", "lastModifiedBy", "category"):
        setattr(wb.properties, a, None)
    wb.save(dst)


def redact_dicom(src: Path, dst: Path, values: list[str]):
    import pydicom
    from textmatch import contains
    ds = pydicom.dcmread(src)
    ds.remove_private_tags()
    for elem in ds.iterall():
        if elem.tag == (0x7FE0, 0x0010) or elem.VR in ("OB", "OW", "SQ", "UN"):
            continue
        if any(contains(str(elem.value), x) for x in values):
            elem.value = ""
    ds.save_as(dst, enforce_file_format=True)


def run(kit: Path, out: Path, mode: str) -> dict:
    index = json.loads((kit / "ground_truth" / "corpus_index.json").read_text(encoding="utf-8"))
    shutil.rmtree(out, ignore_errors=True)
    log = {}
    for d in index["documents"]:
        src = kit / d["file"]
        case_out = out / d["case_key"]; case_out.mkdir(parents=True, exist_ok=True)
        gt = json.loads((kit / d["ground_truth"]).read_text(encoding="utf-8"))
        values = [i["value"] for i in gt["items"] if i["requirement"] != "must_keep"] if mode == "oracle" else []
        kind = sniff(src)
        try:
            if kind == "pdf":
                dst = case_out / src.name
                if mode == "oracle":
                    redact_pdf(src, dst, values)
                else:
                    open_pdf(src).close(); shutil.copy(src, dst)   # still rejects encrypted/corrupt
            elif kind == "image":
                dst = case_out / (src.stem + ".pdf")
                image_to_pdf(src, dst)
            elif kind == "xlsx":
                dst = case_out / src.name
                redact_xlsx(src, dst, values) if mode == "oracle" else shutil.copy(src, dst)
            elif kind == "dicom":
                dst = case_out / src.name
                redact_dicom(src, dst, values) if mode == "oracle" else shutil.copy(src, dst)
            else:
                raise Rejected("UNSUPPORTED_TYPE")
            log[d["doc_id"]] = {"status": "processed", "type": kind, "output": dst.name,
                                "extension_mismatch": kind == "image" and src.suffix.lower() == ".pdf"}
        except Rejected as r:
            log[d["doc_id"]] = {"status": "rejected", "reason": str(r)}
    (out / "processing_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kit", default=".")
    ap.add_argument("--mode", choices=["null", "oracle"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    for k, v in run(Path(a.kit), Path(a.out), a.mode).items():
        print(k, v)


if __name__ == "__main__":
    main()
