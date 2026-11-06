"""PHI detectors (mvp-flow-1.md §7.4).

    labels     a value right after a label ("DOB: 24/07/1955", "Pat.-Nr. 8859641")
    patterns   custom regexes from deid_policy.yaml (e-mail, phones, ID formats, Dr names,
               facilities)
    address    short letterhead/footer segments that contain a postcode
    known      values found anywhere in the same case (by labels or patterns), searched
               for again in every unit, plus the patient's and next of kin's name tokens
    ner        Presidio PERSON / LOCATION, English text only

All findings then pass the policy: allow-listed text is cut out, and under `dob_only`
nothing that looks like a date is redacted unless it is a known date of birth.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any

from adjud.deid.pagetext import GAP, TextUnit
from adjud.deid.policy import PROPAGATE, Policy
from adjud.deid.textmatch import find_all

NAME_TOKEN = r"(?:[A-ZÄÖÜ][a-zäöüß'’-]+|[A-ZÄÖÜ]{2,}(?:-[A-ZÄÖÜ]{2,})?|[A-Z]\.)"  # noqa: S105
HONORIFIC_WORDS = frozenset({"Mr", "Mrs", "Ms", "Miss", "Mx", "Dr", "Prof", "Herr", "Frau", "Med"})
HONORIFIC = r"(?:(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Herr|Frau)\.?(?: med\.)? )"
NAME_RE = re.compile(rf"{HONORIFIC}?(?P<name>{NAME_TOKEN}(?:,? {NAME_TOKEN}){{0,3}})")
# Dates as printed, plus OCR variants with stray spaces or a comma ("18. 10, 1959").
DATE_RE = re.compile(r"\d{1,2} ?[./,] ?\d{1,2} ?[./,] ?\d{2,4}|\d{4}-\d{2}-\d{2}|\d{8}")
ID_RE = re.compile(r"[A-Z0-9][A-Z0-9/-]{2,}(?: \d{3,4}){0,2}")
PHONE_RE = re.compile(r"\+?\d[\d ()/-]{5,}\d")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ADDRESS_RE = re.compile(r"[^\t\n·|]+")
ANY_DATE = re.compile(r"\b(?:\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{4}-\d{2}-\d{2})\b")

PARSER_BY_CATEGORY = {
    "PATIENT_NAME": "name",
    "NEXT_OF_KIN": "name",
    "STAFF_NAME": "staff",
    "DOB": "date",
    "MRN": "id",
    "INSURANCE_NO": "id",
    "CASE_REFERENCE": "id",
    "ADDRESS": "address",
    "PHONE": "phone",
    "EMAIL": "email",
}
NAME_STOPWORDS = frozenset({"Name", "Patient", "Date", "Page", "Seite", "MRN", "DOB", "Tel"})

GERMAN = frozenset(
    "der die das und mit wurde am vom nicht keine oder ein eine im zur zum bei nach seit "
    "herr frau patient patientin entlassung aufgenommen".split()
)
ENGLISH = frozenset(
    "the and was with of on in to his her he she at for from after patient admitted "
    "discharged showed".split()
)


@dataclass
class Finding:
    start: int
    end: int
    category: str
    detector: str
    text: str
    score: float = 1.0
    source: str = ""

    def as_report(self, requirement: str) -> dict[str, Any]:
        d = asdict(self)
        d["requirement"] = requirement
        d["masked"] = mask(self.text)
        del d["text"]  # reports never repeat the identifier itself
        return d


def mask(text: str) -> str:
    """Partly masked text for the site's findings table: 'Philip Roberts' -> 'Ph… Ro…'."""
    words = text.split()
    return " ".join(w[:2] + "…" if len(w) > 2 else "…" for w in words) or "…"


def language_of(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    de = sum(w in GERMAN for w in words)
    en = sum(w in ENGLISH for w in words)
    return "de" if de > en else "en"


# --- case-level knowledge -------------------------------------------------------------------


@dataclass
class KnownValues:
    """Identifiers learned from any document of the case, searched for in all of them."""

    values: dict[str, str] = field(default_factory=dict)  # value -> category
    name_tokens: dict[str, str] = field(default_factory=dict)  # Token -> category

    def add(self, category: str, value: str) -> None:
        value = value.strip(" ,;:.")
        if category not in PROPAGATE or len(value) < 3:
            return
        self.values.setdefault(value, category)
        if category in ("PATIENT_NAME", "NEXT_OF_KIN"):
            for tok in re.split(r"[\s,^]+", value):
                tok = tok.strip(".")
                if len(tok) >= 3 and tok.title() not in HONORIFIC_WORDS:
                    self.name_tokens.setdefault(tok.title(), category)

    def merge(self, other: KnownValues) -> None:
        for v, c in other.values.items():
            self.values.setdefault(v, c)
        for t, c in other.name_tokens.items():
            self.name_tokens.setdefault(t, c)

    def find(self, unit: TextUnit) -> list[Finding]:
        text = unit.text
        out = [
            Finding(s, e, cat, "known", text[s:e], source=unit.source)
            for value, cat in self.values.items()
            for s, e in find_all(text, value)
        ]
        for value, cat in self.values.items():
            if (fuzzy := _ocr_tolerant(value)) is not None:
                for m in fuzzy.finditer(text):
                    out.append(
                        Finding(m.start(), m.end(), cat, "known", m.group(), source=unit.source)
                    )
        for tok, cat in self.name_tokens.items():
            # Title or UPPER case only, so a surname like "Ward" does not hit "ward".
            pat = rf"(?<![\w])(?:[A-Z]\. ?)?(?:{re.escape(tok)}|{re.escape(tok.upper())})(?![\w])"
            for m in re.finditer(pat, text):
                out.append(Finding(m.start(), m.end(), cat, "known", m.group(), source=unit.source))
        return out


_OCR_DIGIT = {"0": "0Oo", "1": "1lI|", "2": "2Z", "5": "5S", "6": "6b", "8": "8B", "9": "9g"}
_OCR_SEP = r"[\s.,/\\|:'-]{0,3}"


@lru_cache(maxsize=1024)
def _ocr_tolerant(value: str) -> re.Pattern[str] | None:
    """For digit-heavy identifiers (DOB, MRN, phone, insurance no.): a pattern that also
    matches common OCR damage: spaces inside digit groups, a separator read as a
    different mark or dropped, and look-alike characters (0/O, 1/l, 5/S, 8/B)."""
    digits = re.findall(r"\d+", value)
    if sum(map(len, digits)) < 6 or len(re.sub(r"[\d\s./,:-]", "", value)) > 3:
        return None
    groups = []
    for g in digits:
        groups.append(" ?".join(f"[{re.escape(_OCR_DIGIT.get(d, d))}]" for d in g))
    prefix = re.escape(value[: value.index(digits[0])].strip()) if value[0].isalpha() else ""
    body = _OCR_SEP.join(groups)
    if prefix:  # a letter prefix is often lost by OCR ("J769144633" -> "769144633")
        body = rf"(?:{prefix}\s?-?\s?)?" + body
    return re.compile(rf"(?<![\w]){body}(?![\w])")


# --- detectors ------------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _label_regex(labels: tuple[str, ...]) -> re.Pattern[str]:
    long = sorted((lab for lab in labels if len(lab) > 3), key=len, reverse=True)
    short = sorted((lab for lab in labels if len(lab) <= 3), key=len, reverse=True)
    alts = []
    if long:
        alts.append(rf"(?P<long>{'|'.join(map(re.escape, long))})(?![\w])(?:[ ]*[:.,])?[ \t]*")
    if short:
        alts.append(rf"(?P<short>{'|'.join(map(re.escape, short))})(?:[ ]*[:.][ \t]*|\t[ \t]*)")
    return re.compile(rf"(?<![\w.-])(?:{'|'.join(alts)})", re.IGNORECASE)


def _parse_value(
    kind: str, text: str, pos: int, stop_words: frozenset[str] = frozenset()
) -> tuple[int, int] | None:
    rest = text[pos:]
    if kind in ("name", "staff"):
        m = NAME_RE.match(rest)
        if not m:
            return None
        name = m.group("name").rstrip(",")
        tokens = name.split(" ")
        cursor = m.start("name")
        for i, tok in enumerate(tokens):  # a name never runs into the next label:
            cursor = rest.index(tok, cursor) + len(tok)  # a label word, or any word
            followed_by_colon = rest[cursor : cursor + 1] == ":"  # with a colon ("DOS:")
            if tok.strip(".,:").casefold() in stop_words or followed_by_colon:
                name = " ".join(tokens[:i]).rstrip(",")
                break
        if not name:
            return None
        if all(t.strip(".,") in NAME_STOPWORDS for t in name.split()):
            return None
        start = pos + (m.start() if kind == "staff" else m.start("name"))
        return start, pos + m.start("name") + len(name)
    regex = {"date": DATE_RE, "id": ID_RE, "phone": PHONE_RE, "email": EMAIL_RE,
             "address": ADDRESS_RE}[kind]  # fmt: skip
    m = regex.match(rest)
    if not m:
        return None
    value = m.group().rstrip(" ,;")
    if kind == "id" and not re.search(r"\d", value):
        return None
    if kind == "address" and (len(value) < 6 or not re.search(r"\d", value)):
        return None
    return pos + m.start(), pos + m.start() + len(value)


def _plausible_dob(value: str, min_age_years: int) -> bool:
    years = re.findall(r"\d{4}", value.replace(" ", ""))
    if not years:
        return True  # 2-digit years: cannot tell, keep it as a DOB
    return int(years[-1]) <= date.today().year - min_age_years


def detect_labels(unit: TextUnit, policy: Policy) -> list[Finding]:
    out = []
    stop_words = policy.label_words()
    for category, labels in policy.labels.items():
        kind = PARSER_BY_CATEGORY.get(category)
        if kind is None:
            continue
        for m in _label_regex(tuple(labels)).finditer(unit.text):
            span = _parse_value(kind, unit.text, m.end(), stop_words)
            if not span:
                continue
            s, e = span
            value = unit.text[s:e]
            if category == "DOB" and not _plausible_dob(value, policy.dob_min_age_years):
                continue
            out.append(Finding(s, e, category, "label", value, source=unit.source))
    return out


def detect_patterns(unit: TextUnit, policy: Policy) -> list[Finding]:
    return [
        Finding(m.start(), m.end(), p.category, "pattern", m.group(), source=unit.source)
        for p in policy.patterns
        for m in p.regex.finditer(unit.text)
    ]


def detect_address_segments(unit: TextUnit, policy: Policy) -> list[Finding]:
    out = []
    # Segments end at a column gap, a line end, "·", "|" or a spaced dash (OCR often
    # reads "·" as "-").
    for m in re.finditer(r"(?:[^\t\n·| ]|[ ](?![-–—][ ]))+", unit.text):
        seg = m.group()
        if len(seg.split()) <= policy.address_max_words and policy.postcode.search(seg):
            lead = len(seg) - len(seg.lstrip())
            s, e = m.start() + lead, m.start() + len(seg.rstrip())
            out.append(Finding(s, e, "FACILITY", "address", unit.text[s:e], source=unit.source))
    return out


@lru_cache(maxsize=1)
def _analyzer():  # pragma: no cover - heavy, exercised by the corpus tests
    from presidio_analyzer import AnalyzerEngine

    return AnalyzerEngine(supported_languages=["en"])


def detect_ner(unit: TextUnit, policy: Policy) -> list[Finding]:
    if not policy.ner_entities or not unit.text.strip():
        return []
    results = _analyzer().analyze(
        text=unit.text, language="en", entities=policy.ner_entities,
        score_threshold=policy.ner_min_score,
    )  # fmt: skip
    out = []
    for r in results:
        s, e = r.start, r.end
        piece = unit.text[s:e]
        cut = re.search(r"[\n\t]", piece)  # never let an entity run across lines/columns
        if cut:
            piece = piece[: cut.start()]
            e = s + len(piece)
        piece = piece.rstrip(" ,.;:")
        e = s + len(piece)
        if len(piece) < 3 or ANY_DATE.search(piece):
            continue
        if r.entity_type == "PERSON" and re.search(r"\d", piece):
            continue
        category = "PERSON" if r.entity_type == "PERSON" else "LOCATION"
        out.append(Finding(s, e, category, "ner", piece, float(r.score), source=unit.source))
    return out


# --- policy filters ---------------------------------------------------------------------------


def _subtract(f: Finding, allowed: list[tuple[int, int]], text: str) -> list[Finding]:
    pieces = [(f.start, f.end)]
    for a0, a1 in allowed:
        nxt = []
        for s, e in pieces:
            if a1 <= s or a0 >= e:
                nxt.append((s, e))
                continue
            if s < a0:
                nxt.append((s, a0))
            if a1 < e:
                nxt.append((a1, e))
        pieces = nxt
    out = []
    for s, e in pieces:
        seg = text[s:e]
        lead, trail = len(seg) - len(seg.lstrip(" ,.;:-()")), len(seg.rstrip(" ,.;:-()"))
        s2, e2 = s + lead, s + trail
        if e2 - s2 >= 2 and re.search(r"[\wÄÖÜäöüß]", text[s2:e2]):
            out.append(Finding(s2, e2, f.category, f.detector, text[s2:e2], f.score, f.source))
    return out


def apply_policy(findings: list[Finding], unit: TextUnit, policy: Policy,
                 known: KnownValues) -> list[Finding]:  # fmt: skip
    allowed = policy.allowed_spans(unit.text)
    out = []
    for f in findings:
        if f.category != "DOB" and f.detector != "known" and ANY_DATE.fullmatch(f.text.strip()):
            continue  # dob_only: clinical dates are kept
        out.extend(_subtract(f, allowed, unit.text) if allowed else [f])
    return out


def detect(unit: TextUnit, policy: Policy, known: KnownValues, *, use_ner: bool = True,
           language: str | None = None) -> list[Finding]:  # fmt: skip
    """All findings in one unit, after the policy filters (not merged)."""
    findings = (
        detect_labels(unit, policy)
        + detect_patterns(unit, policy)
        + detect_address_segments(unit, policy)
        + known.find(unit)
    )
    lang = language or language_of(unit.text)
    if use_ner and lang in policy.ner_languages:
        findings += detect_ner(unit, policy)
    return apply_policy(findings, unit, policy, known)


def learn(unit: TextUnit, policy: Policy, known: KnownValues) -> None:
    """Pass 1: add every labelled or pattern-matched identifier to the case's knowledge."""
    for f in apply_policy(detect_labels(unit, policy) + detect_patterns(unit, policy), unit,
                          policy, known):  # fmt: skip
        known.add(f.category, f.text)


def merge_spans(findings: list[Finding]) -> list[tuple[int, int]]:
    spans = sorted((f.start, f.end) for f in findings)
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(e, merged[-1][1]))
        else:
            merged.append((s, e))
    return merged


__all__ = ["GAP", "Finding", "KnownValues", "detect", "language_of", "learn", "mask"]
