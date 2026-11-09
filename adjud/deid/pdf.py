"""PDF and image redaction, metadata scrub and verification.

Text-only pages: PyMuPDF redaction annotations over every finding's words, then
`apply_redactions`, which deletes the underlying characters (they are not just covered).

Pages with images (scans, image banners) and pages without a text layer: the page is
rendered at 300 dpi, OCRed, findings from OCR *and* from the text layer are blacked out,
and the page is rebuilt as an image-only page. No hidden text layer is kept.

Image files (PNG, JPG, TIFF) are made upright, straightened and output as PDF pages.

The output document is built fresh and scrubbed: no metadata, XMP, embedded files,
JavaScript, links or annotations.
"""

from __future__ import annotations

import hashlib
import io
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pymupdf
from PIL import Image, ImageSequence

from adjud.deid import ocr
from adjud.deid.detect import Finding, KnownValues, detect, finding_id, learn, merge_spans
from adjud.deid.pagetext import TextUnit
from adjud.deid.policy import Policy

# Metadata keys that never hold identifiers (format/producer info).
_HARMLESS_META = {"format", "encryption", "producer", "creator", "creationDate", "modDate"}
RENDER_DPI = 300


def page_units(doc: pymupdf.Document) -> list[TextUnit]:
    return [
        TextUnit.from_words([w[:5] for w in page.get_text("words")], source=f"page:{i + 1}")
        for i, page in enumerate(doc)
    ]


def metadata_units(doc: pymupdf.Document) -> list[TextUnit]:
    units = [
        TextUnit.from_string(str(v), source=f"metadata:{k}")
        for k, v in (doc.metadata or {}).items()
        if v and k not in _HARMLESS_META
    ]
    xml = doc.get_xml_metadata()
    if xml:
        units.append(TextUnit.from_string(xml, source="metadata:xmp"))
    return units


def needs_ocr(page: pymupdf.Page) -> bool:
    return bool(page.get_images(full=True)) or not page.get_text().strip()


def _render(page: pymupdf.Page) -> Image.Image:
    pix = page.get_pixmap(dpi=RENDER_DPI, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


# --- OCR cache: learn and redact passes OCR the same pages once ------------------------------

_CACHE: OrderedDict[tuple[str, int], ocr.OcrPage] = OrderedDict()
_CACHE_SIZE = 24


def _cached(key: tuple[str, int], make) -> ocr.OcrPage:
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    value = make()
    _CACHE[key] = value
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def ocr_pdf_page(content: bytes, page: pymupdf.Page) -> ocr.OcrPage:
    # A page with a text layer keeps its geometry (no rotate/deskew) so text-layer boxes
    # still line up; a pure image page is made upright and straightened first.
    has_text = bool(page.get_text().strip())
    return _cached(
        (_digest(content), page.number),
        lambda: ocr.prepare(_render(page), RENDER_DPI, upright=not has_text),
    )


def image_frames(content: bytes) -> list[tuple[Image.Image, float | None]]:
    with Image.open(io.BytesIO(content)) as img:
        dpi = (img.info.get("dpi") or (None,))[0]
        return [(frame.convert("L"), dpi) for frame in ImageSequence.Iterator(img)]


def ocr_image_frame(content: bytes, n: int, frame: Image.Image, dpi: float | None) -> ocr.OcrPage:
    return _cached((_digest(content), n), lambda: ocr.prepare(frame, dpi, upright=True))


# --- pass 1: learn ---------------------------------------------------------------------------


def learn_pdf(content: bytes, policy: Policy, known: KnownValues) -> None:
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for page, unit in zip(doc, page_units(doc), strict=True):
            learn(unit, policy, known)
            if needs_ocr(page) and ocr.available():
                for u in ocr_pdf_page(content, page).units:
                    learn(u, policy, known)
        for unit in metadata_units(doc):
            learn(unit, policy, known)


def learn_image(content: bytes, policy: Policy, known: KnownValues) -> None:
    if not ocr.available():
        return
    for n, (frame, dpi) in enumerate(image_frames(content)):
        for u in ocr_image_frame(content, n, frame, dpi).units:
            learn(u, policy, known)


# --- pass 2: redact --------------------------------------------------------------------------


@dataclass
class PdfOutcome:
    output: bytes
    findings: list[Finding] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)  # 1-based pages rebuilt from OCR
    unverifiable_pages: list[int] = field(default_factory=list)  # images, but no OCR
    notes: list[str] = field(default_factory=list)
    pages: int = 0
    ai_terms: list[str] = field(default_factory=list)  # AI-1 identifiers located and redacted


def _findings(
    unit: TextUnit, policy: Policy, known: KnownValues, extra: KnownValues, keep: set[str]
) -> list[Finding]:
    """Findings minus those the site chose to keep (`keep` holds finding ids). Keeping a
    finding also keeps every other detector's finding inside the same span (e.g. NER's
    "Amelia Hart" inside the kept "Dr Amelia Hart"). Site-added terms always apply."""
    found = detect(unit, policy, known)
    kept = [(f.start, f.end) for f in found if f.id in keep]
    found = [
        f
        for f in found
        if f.id not in keep and not any(s <= f.start and f.end <= e for s, e in kept)
    ]
    return found + extra.find(unit)


AiDetect = Callable[[int, Image.Image], list[tuple[str, str]]]  # (page, image) -> (text, cat)


def _ai_findings(
    ai_detect: AiDetect | None, page_no: int, op: ocr.OcrPage, outcome: PdfOutcome
) -> list[tuple[TextUnit, list[Finding]]]:
    """AI-1 (plan step 20): identifiers the agent saw on the unredacted page image, located
    in the page's OCR text. They only ever add redaction; one that cannot be found in the
    OCR text cannot be boxed and is reported as a warning for the site."""
    if ai_detect is None:
        return []
    items = ai_detect(page_no, op.image)
    if not items:
        return []
    known = KnownValues()
    for text, category in items:
        known.values.setdefault(text.strip(), category)
    located: list[tuple[TextUnit, list[Finding]]] = []
    hit: set[str] = set()
    for u in op.units:
        found = known.find(u)
        for f in found:
            f.detector = "ai"
            hit.add(next((v for v in known.values if f.text.lower() in v.lower()
                           or v.lower() in f.text.lower()), f.text))  # fmt: skip
        located.append((u, found))
    missed = [v for v in known.values if v not in hit]
    outcome.ai_terms += [v for v in known.values if v in hit]
    if missed:
        outcome.notes.append(f"page {page_no}: AI saw {len(missed)} identifier(s) OCR could "
                             "not place; check this page")  # fmt: skip
    return located


def _extra(terms: list[str] | None) -> KnownValues:
    extra = KnownValues()
    for term in terms or []:
        if term.strip():
            extra.values[term.strip()] = "SITE_TERM"
    return extra


Box = tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the output page


def _box_px(boxes: list[Box], width: float, height: float) -> list[Box]:
    """Site-drawn boxes (fractions of the page) in pixels or points of a page this size."""
    return [(x0 * width, y0 * height, x1 * width, y1 * height) for x0, y0, x1, y1 in boxes]


def _image_page(out: pymupdf.Document, img: Image.Image, dpi: float) -> None:
    page = out.new_page(width=img.width * 72 / dpi, height=img.height * 72 / dpi)
    page.insert_image(page.rect, stream=ocr.png_bytes(img))


def _finish(out: pymupdf.Document) -> bytes:
    out.scrub()  # metadata, XMP, embedded files, JS, links, annotations, hidden text
    out.set_metadata({})
    out.del_xml_metadata()
    return out.tobytes(garbage=4, deflate=True, clean=True)


def redact_pdf(
    content: bytes,
    policy: Policy,
    known: KnownValues,
    *,
    extra_terms: list[str] | None = None,
    keep: list[str] | None = None,
    boxes: dict[int, list[Box]] | None = None,
    ai_detect: AiDetect | None = None,
) -> PdfOutcome:
    """Redact every finding. `extra_terms` are site-added terms (F1-22); `keep` holds the
    ids of findings the site unticked with a reason (F1-21); `boxes` are areas the site drew
    on the output preview, by 1-based page (plan P-30). Processing the same original always
    gives the same page geometry, so a box drawn on one run's output fits the next run."""
    extra, keep_set, boxes = _extra(extra_terms), set(keep or []), boxes or {}
    outcome = PdfOutcome(output=b"")
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        outcome.pages = doc.page_count
        units = page_units(doc)
        outcome.findings += [f for u in metadata_units(doc) for f in detect(u, policy, known)]
        rebuilt: dict[int, tuple[Image.Image, float]] = {}
        for page, unit in zip(doc, units, strict=True):
            text_findings = _findings(unit, policy, known, extra, keep_set)
            outcome.findings += text_findings
            if needs_ocr(page) and ocr.available():
                op = ocr_pdf_page(content, page)
                rects = []
                for u in op.units:
                    img_findings = _findings(u, policy, known, extra, keep_set)
                    outcome.findings += img_findings
                    rects += [r for s, e in merge_spans(img_findings) for r in u.rects_for(s, e)]
                scale = op.dpi / 72  # text-layer boxes -> pixels (geometry kept, see above)
                rects += [
                    (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
                    for s, e in merge_spans(text_findings)
                    for x0, y0, x1, y1 in unit.rects_for(s, e)
                ]
                rects += _box_px(boxes.get(page.number + 1, []), *op.image.size)
                for u, found in _ai_findings(ai_detect, page.number + 1, op, outcome):
                    outcome.findings += found
                    rects += [r for s, e in merge_spans(found) for r in u.rects_for(s, e)]
                rebuilt[page.number] = (ocr.black_out(op.image, rects), op.dpi)
                outcome.ocr_pages.append(page.number + 1)
                outcome.notes += [f"page {page.number + 1}: {n}" for n in op.notes]
                continue
            if needs_ocr(page):
                outcome.unverifiable_pages.append(page.number + 1)
            for s, e in merge_spans(text_findings):
                for rect in unit.rects_for(s, e):
                    page.add_redact_annot(pymupdf.Rect(rect), fill=(0, 0, 0))
            for rect in _box_px(boxes.get(page.number + 1, []), page.rect.width, page.rect.height):
                page.add_redact_annot(pymupdf.Rect(rect) * page.derotation_matrix, fill=(0, 0, 0))
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        with pymupdf.open() as out:
            for page in doc:
                if page.number in rebuilt:
                    _image_page(out, *rebuilt[page.number])
                else:
                    out.insert_pdf(doc, from_page=page.number, to_page=page.number)
            outcome.output = _finish(out)
    return outcome


def redact_image(
    content: bytes,
    policy: Policy,
    known: KnownValues,
    *,
    extra_terms: list[str] | None = None,
    keep: list[str] | None = None,
    boxes: dict[int, list[Box]] | None = None,
    ai_detect: AiDetect | None = None,
) -> PdfOutcome:
    """PNG/JPG/TIFF -> upright, straightened, redacted image-only PDF (one page per frame).
    `boxes`: site-drawn areas by 1-based page, as in `redact_pdf`."""
    extra, keep_set, boxes = _extra(extra_terms), set(keep or []), boxes or {}
    outcome = PdfOutcome(output=b"")
    frames = image_frames(content)
    outcome.pages = len(frames)
    with pymupdf.open() as out:
        for n, (frame, dpi) in enumerate(frames):
            if not ocr.available():
                outcome.unverifiable_pages.append(n + 1)
                drawn = ocr.black_out(frame, _box_px(boxes.get(n + 1, []), *frame.size))
                _image_page(out, drawn, float(dpi or RENDER_DPI))
                continue
            op = ocr_image_frame(content, n, frame, dpi)
            rects = []
            for u in op.units:
                findings = _findings(u, policy, known, extra, keep_set)
                for f in findings:
                    f.source = f"page:{n + 1}"
                outcome.findings += findings
                rects += [r for s, e in merge_spans(findings) for r in u.rects_for(s, e)]
            rects += _box_px(boxes.get(n + 1, []), *op.image.size)
            for u, found in _ai_findings(ai_detect, n + 1, op, outcome):
                for f in found:
                    f.source = f"page:{n + 1}"
                outcome.findings += found
                rects += [r for s, e in merge_spans(found) for r in u.rects_for(s, e)]
            _image_page(out, ocr.black_out(op.image, rects), op.dpi)
            outcome.ocr_pages.append(n + 1)
            outcome.notes += [f"page {n + 1}: {note}" for note in op.notes]
        outcome.output = _finish(out)
    return outcome


# --- verification ---------------------------------------------------------------------------


@dataclass
class PdfVerification:
    residuals: list[Finding] = field(default_factory=list)
    metadata_left: list[str] = field(default_factory=list)
    embedded_files: list[str] = field(default_factory=list)
    unverified_pages: list[int] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (
            self.residuals or self.metadata_left or self.embedded_files or self.unverified_pages
        )

    def summary(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "residuals": len(self.residuals),
            "metadata_left": self.metadata_left,
            "embedded_files": self.embedded_files,
            "unverified_pages": self.unverified_pages,
        }


def verify_pdf(
    output: bytes,
    policy: Policy,
    known: KnownValues,
    *,
    keep: list[str] | None = None,
    seeded: list[str] | None = None,
    extra_terms: list[str] | None = None,
) -> PdfVerification:
    """Check the OUTPUT from scratch (§7.8): re-extract text (and fresh OCR of every image
    page), re-run every detector, check metadata, and, for synthetic data, search for the
    generator's seeded identifiers. Texts the site deliberately kept do not count."""
    from adjud.deid.textmatch import contains

    result = PdfVerification()
    extra, keep_set = _extra(extra_terms), set(keep or [])
    with pymupdf.open(stream=output, filetype="pdf") as doc:
        for page, unit in zip(doc, page_units(doc), strict=True):
            units = [unit]
            if needs_ocr(page):
                if ocr.available():
                    units += ocr.ocr_units(_render(page), RENDER_DPI, source=unit.source)
                else:
                    result.unverified_pages.append(page.number + 1)
            for u in units:
                result.residuals += _findings(u, policy, known, extra, keep_set)
                for value in seeded or []:
                    if finding_id(value) not in keep_set and contains(u.text, value):
                        result.residuals.append(
                            Finding(0, 0, "SEEDED", "seeded", value, source=unit.source)
                        )
        result.metadata_left = [
            k for k, v in (doc.metadata or {}).items() if v and k not in _HARMLESS_META
        ]
        if doc.get_xml_metadata():
            result.metadata_left.append("xmp")
        result.embedded_files = list(doc.embfile_names())
    return result
