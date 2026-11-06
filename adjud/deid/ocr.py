"""OCR for scanned and image pages (mvp-flow-1.md §7.1–7.3, §7.5).

The image we OCR is the image we output: it is first made upright (Tesseract OSD, 90°
steps), straightened (small skew, by projection profile) and brought to about 300 dpi.
Word boxes therefore line up exactly with the black boxes drawn on the output page.
Dark images with light text (e.g. an X-ray annotation) are inverted for OCR only.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pytesseract
from PIL import Image, ImageDraw, ImageOps

from adjud.deid.pagetext import TextUnit

TARGET_DPI = 300
MIN_CONFIDENCE = 30.0


@lru_cache(maxsize=1)
def available() -> bool:
    try:
        pytesseract.get_tesseract_version()
    except (pytesseract.TesseractNotFoundError, OSError):
        return False
    return True


@lru_cache(maxsize=1)
def languages() -> str:
    """eng+deu when German is installed (P-04), else eng."""
    try:
        langs = set(pytesseract.get_languages(config=""))
    except (pytesseract.TesseractError, OSError):
        return "eng"
    return "+".join(lang for lang in ("eng", "deu") if lang in langs) or "eng"


def _is_dark(img: Image.Image) -> bool:
    return float(np.asarray(img.convert("L"), dtype=np.float32).mean()) < 110


def _ocr_view(img: Image.Image) -> Image.Image:
    grey = img.convert("L")
    return ImageOps.invert(grey) if _is_dark(grey) else grey


def _osd_rotation(img: Image.Image) -> int:
    """Clockwise degrees Tesseract says the page is rotated by (0, 90, 180, 270)."""
    try:
        osd = pytesseract.image_to_osd(_ocr_view(img), output_type=pytesseract.Output.DICT)
        if float(osd.get("orientation_conf", 0)) < 1.0:
            return 0
        return int(osd.get("rotate", 0)) % 360
    except pytesseract.TesseractError:
        return 0


def _skew_angle(img: Image.Image, limit: float = 5.0, step: float = 0.25) -> float:
    """Small-angle skew by maximising the variance of row sums of the ink."""
    small = _ocr_view(img)
    small.thumbnail((1000, 1000))
    ink = 255 - np.asarray(small, dtype=np.float32)
    best, best_score = 0.0, -1.0
    pil = Image.fromarray(ink.astype(np.uint8))
    for angle in np.arange(-limit, limit + step, step):
        rows = np.asarray(pil.rotate(float(angle), resample=Image.BILINEAR), dtype=np.float32)
        score = float(np.var(rows.sum(axis=1)))
        if score > best_score:
            best, best_score = float(angle), score
    return best


@dataclass
class OcrPage:
    image: Image.Image  # upright, straightened, ~300 dpi; this is what gets output
    unit: TextUnit  # OCR words with boxes in `image` pixels
    rotation: int = 0
    skew: float = 0.0
    dpi: float = TARGET_DPI
    notes: list[str] = field(default_factory=list)


def prepare(img: Image.Image, dpi: float | None, *, upright: bool = True) -> OcrPage:
    """Normalise a page image and OCR it."""
    img = img.convert("RGB")
    dpi = float(dpi or TARGET_DPI)
    if dpi < TARGET_DPI * 0.9:  # low-resolution scans: upscale for OCR and output
        factor = TARGET_DPI / dpi
        img = img.resize((round(img.width * factor), round(img.height * factor)), Image.LANCZOS)
        dpi = TARGET_DPI
    rotation = skew = 0.0
    notes = []
    if upright:
        rotation = _osd_rotation(img)
        if rotation:
            img = img.rotate(-rotation, expand=True, fillcolor="white")
            notes.append(f"rotated {rotation}° to upright")
        skew = _skew_angle(img)
        if abs(skew) >= 0.25:
            fill = "black" if _is_dark(img) else "white"
            img = img.rotate(skew, resample=Image.BICUBIC, expand=False, fillcolor=fill)
            notes.append(f"deskewed {skew:+.2f}°")
    return OcrPage(
        image=img, unit=ocr_unit(img), rotation=int(rotation), skew=skew, dpi=dpi, notes=notes
    )


def ocr_unit(img: Image.Image, source: str = "") -> TextUnit:
    data = pytesseract.image_to_data(
        _ocr_view(img),
        lang=languages(),
        config="--psm 3",
        output_type=pytesseract.Output.DICT,
    )
    words = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text or float(data["conf"][i]) < MIN_CONFIDENCE:
            continue
        x, y, w, h = (data[k][i] for k in ("left", "top", "width", "height"))
        words.append((float(x), float(y), float(x + w), float(y + h), text))
    # A wide column gap at 300 dpi is ~50 px (12 pt at 72 dpi -> 50 px at 300 dpi).
    return TextUnit.from_words(words, gap=50.0, source=source)


def black_out(
    img: Image.Image, rects: list[tuple[float, float, float, float]], pad: float = 4.0
) -> Image.Image:
    out = img.copy()
    draw = ImageDraw.Draw(out)
    for x0, y0, x1, y1 in rects:
        draw.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill="black")
    return out


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
