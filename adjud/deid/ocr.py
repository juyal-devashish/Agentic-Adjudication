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
from PIL import Image, ImageDraw, ImageFilter, ImageOps

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
    """Small-angle skew by maximising the variance of row sums of the ink. Returns 0
    unless the estimate is clear: at least 1°, not at the search limit, and a clearly
    better profile than no rotation (images such as X-rays have no text lines to align)."""
    small = _ocr_view(img)
    small.thumbnail((1000, 1000))
    ink = Image.fromarray((255 - np.asarray(small, dtype=np.float32)).astype(np.uint8))

    def score(angle: float) -> float:
        rows = np.asarray(ink.rotate(angle, resample=Image.BILINEAR), dtype=np.float32)
        return float(np.var(rows.sum(axis=1)))

    angles = [float(a) for a in np.arange(-limit, limit + step, step)]
    scores = {a: score(a) for a in angles}
    best = max(scores, key=scores.get)
    if abs(best) < 1.0 or abs(best) >= limit - step or scores[best] < 1.2 * scores[0.0]:
        return 0.0
    return best


@dataclass
class OcrPage:
    image: Image.Image  # upright, straightened; this is what gets output
    units: list[TextUnit]  # OCR words with boxes in `image` pixels, one unit per OCR pass
    rotation: int = 0
    skew: float = 0.0
    dpi: float = TARGET_DPI
    notes: list[str] = field(default_factory=list)

    @property
    def unit(self) -> TextUnit:
        return self.units[0]


def prepare(img: Image.Image, dpi: float | None, *, upright: bool = True) -> OcrPage:
    """Normalise a page image (orientation, skew) and OCR it at its native resolution:
    upscaling a noisy low-resolution scan enlarges the noise and OCR gets worse."""
    img = img.convert("L")
    dpi = float(dpi or TARGET_DPI)
    rotation, skew, notes = 0, 0.0, []
    if upright:
        rotation = _osd_rotation(img)
        if rotation:
            img = img.rotate(-rotation, expand=True, fillcolor=255)
            notes.append(f"rotated {rotation}° to upright")
        skew = _skew_angle(img)
        if skew:
            fill = 0 if _is_dark(img) else 255
            img = img.rotate(skew, resample=Image.BICUBIC, expand=False, fillcolor=fill)
            notes.append(f"deskewed {skew:+.2f}°")
    return OcrPage(
        image=img, units=ocr_units(img, dpi), rotation=rotation, skew=skew, dpi=dpi, notes=notes
    )


def ocr_units(img: Image.Image, dpi: float = TARGET_DPI, source: str = "") -> list[TextUnit]:
    """Several OCR passes; findings from all of them are used, so a word misread in one
    pass can still be caught in another:
      - plain, and median-filtered (better on speckled faxes), at native resolution;
      - for low-resolution scans (< 250 dpi), also upscaled 2x, with boxes mapped back.
    """
    units = [
        ocr_unit(img, dpi, source),
        ocr_unit(img.filter(ImageFilter.MedianFilter(3)), dpi, source),
    ]
    if dpi < 250:
        big = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        units.append(_scaled(ocr_unit(big, dpi * 2, source), 0.5))
    return units


def _scaled(unit: TextUnit, factor: float) -> TextUnit:
    unit.word_rects = [tuple(v * factor for v in r) for r in unit.word_rects]
    return unit


def ocr_unit(img: Image.Image, dpi: float = TARGET_DPI, source: str = "") -> TextUnit:
    data = pytesseract.image_to_data(
        _ocr_view(img),
        lang=languages(),
        config="--psm 3",
        output_type=pytesseract.Output.DICT,
    )
    pixels = np.asarray(img.convert("L"))
    words = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text or float(data["conf"][i]) < MIN_CONFIDENCE:
            continue
        x, y, w, h = (data[k][i] for k in ("left", "top", "width", "height"))
        if _inside_redaction(pixels, x, y, w, h):
            continue  # OCR "reading" a black redaction box ("EE", "ee", "|")
        words.append((float(x), float(y), float(x + w), float(y + h), text))
    # A wide column gap is 12 pt; in pixels that depends on the resolution.
    return TextUnit.from_words(words, gap=12.0 * dpi / 72, source=source)


def _inside_redaction(pixels: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    """True if the word box is mostly pure black (value <= 2), i.e. a redaction box.
    Scanned paper, dark X-ray backgrounds (~20) and ink strokes never reach that."""
    box = pixels[max(y, 0) : y + h, max(x, 0) : x + w]
    return box.size > 0 and float((box <= 2).mean()) > 0.6


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
