"""Shared text normalisation and matching used by the generator and the de-identification harness.

Matching is deliberately simple and strict so results are explainable:
- Unicode NFKC, case-folded, dashes unified, DICOM '^' treated as a space, whitespace collapsed.
- '-', '/' and '.' between two digits are removed, so "14/03/1956" matches "14.03.1956".
  Spaces between digit groups are tried both kept and removed, so "999 123 4567" matches
  "9991234567" without gluing a date to a following time.
- A match must not be glued to other letters or digits on either side.
"""
from __future__ import annotations

import re
import unicodedata

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")
_DIGIT_PUNCT = re.compile(r"(?<=\d)[\-/.](?=\d)")
_DIGIT_SPACE = re.compile(r"(?<=\d) (?=\d)")
_SPACE = re.compile(r"\s+")


def norm(text: str, join_digit_spaces: bool = False) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_DASHES)
    text = text.replace("^", " ").casefold()
    text = _SPACE.sub(" ", text)
    text = _DIGIT_PUNCT.sub("", text)
    if join_digit_spaces:
        text = _DIGIT_SPACE.sub("", text)
    return text.strip()


def _search(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    pattern = r"(?<![0-9a-z\u00c0-\u024f])" + re.escape(needle) + r"(?![0-9a-z\u00c0-\u024f])"
    return re.search(pattern, haystack) is not None


def contains(haystack: str, value: str) -> bool:
    """True if `value` occurs in `haystack` after normalisation, on token boundaries.

    Tried twice: keeping spaces between digit groups (so a date followed by a time still
    matches), and joining them (so "01632 960123" matches OCR output "01632960123").
    """
    return (_search(norm(haystack), norm(value))
            or _search(norm(haystack, True), norm(value, True)))
