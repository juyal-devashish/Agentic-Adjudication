"""Normalised, position-preserving text matching.

Uses the same normalisation as the test kit's scorer (data/flow1-testkit/tools/
textmatch.py), so "found" here means the same as "residual" there:
Unicode NFKC, case-folded, dashes unified, '^' as a space, whitespace collapsed,
'-' '/' '.' between digits removed, digit-group spaces tried kept and removed, and
matches must sit on token boundaries.

Unlike the scorer, every normalised character keeps the index of the original character
it came from, so a match can be mapped back to a span (and from there to page boxes).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_LETTER_OR_DIGIT = r"0-9a-zÀ-ɏ"


def _normalise(text: str, join_digit_spaces: bool) -> tuple[str, list[int]]:
    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).translate(_DASHES).replace("^", " ").casefold():
            c = " " if c.isspace() else c
            if c == " " and (not chars or chars[-1] == " "):
                continue  # collapse whitespace, drop leading
            chars.append(c)
            origin.append(i)
    out_c: list[str] = []
    out_o: list[int] = []
    n = len(chars)
    for k, c in enumerate(chars):
        prev_digit = k > 0 and chars[k - 1].isdigit()
        next_digit = k + 1 < n and chars[k + 1].isdigit()
        if prev_digit and next_digit and (c in "-/." or (join_digit_spaces and c == " ")):
            continue
        out_c.append(c)
        out_o.append(origin[k])
    while out_c and out_c[-1] == " ":
        out_c.pop()
        out_o.pop()
    return "".join(out_c), out_o


def norm(text: str, join_digit_spaces: bool = False) -> str:
    return _normalise(text, join_digit_spaces)[0]


@lru_cache(maxsize=4096)
def _pattern(needle: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![{_LETTER_OR_DIGIT}]){re.escape(needle)}(?![{_LETTER_OR_DIGIT}])")


def find_all(text: str, value: str) -> list[tuple[int, int]]:
    """Spans (start, end) in `text` where `value` occurs, by the scorer's rules."""
    spans: set[tuple[int, int]] = set()
    for join in (False, True):
        needle = norm(value, join)
        if not needle:
            continue
        hay, origin = _normalise(text, join)
        for m in _pattern(needle).finditer(hay):
            spans.add((origin[m.start()], origin[m.end() - 1] + 1))
    return sorted(spans)


def contains(text: str, value: str) -> bool:
    return bool(find_all(text, value))
