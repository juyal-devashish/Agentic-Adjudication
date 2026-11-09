"""Do a case's documents describe the same patient? (plan P-32, step 14)

During de-identification each document's identifiers are learned anyway; here the
documents of one case are compared pairwise on what is most telling: the date of birth
and the patient's name. Only the verdict leaves this module ("summary.pdf and ecg.pdf
give different dates of birth"); the identifiers themselves are never stored.

The comparison leans towards *no* conflict when in doubt: a date whose day and month
could be swapped matches either way, two-digit years match on their last two digits, and
a name conflicts only when the two documents share no name token at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations

from adjud.deid.detect import HONORIFIC_WORDS, KnownValues

DateKey = tuple[int, int, int]  # day, month, year modulo 100


def dob_keys(value: str) -> set[DateKey]:
    """Possible (day, month, yy) readings of a printed date; empty if it is not a date."""
    digits = re.findall(r"\d+", value)
    if len(digits) == 1 and len(digits[0]) == 8:  # 24071955 or 19550724
        s = digits[0]
        digits = [s[6:], s[4:6], s[:4]] if s[:2] in ("19", "20") else [s[:2], s[2:4], s[4:]]
    if len(digits) != 3:
        return set()
    a, b, c = (int(x) for x in digits)
    if len(digits[0]) == 4:  # ISO: year first
        a, b, c = c, b, a
    keys = set()
    for day, month in ((a, b), (b, a)):
        if 1 <= day <= 31 and 1 <= month <= 12:
            keys.add((day, month, c % 100))
    return keys


def name_tokens(value: str) -> set[str]:
    return {
        t.title()
        for t in re.split(r"[\s,^.]+", value)
        if len(t) >= 3 and t.title() not in HONORIFIC_WORDS and not any(ch.isdigit() for ch in t)
    }


@dataclass
class DocIdentity:
    dobs: list[set[DateKey]] = field(default_factory=list)
    names: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, known: KnownValues) -> DocIdentity:
        ident = cls()
        for value, category in known.values.items():
            if category == "DOB" and (keys := dob_keys(value)):
                ident.dobs.append(keys)
            elif category == "PATIENT_NAME":
                ident.names |= name_tokens(value)
        return ident

    def dob_conflict(self, other: DocIdentity) -> bool:
        if not self.dobs or not other.dobs:
            return False
        return not any(a & b for a in self.dobs for b in other.dobs)

    def name_conflict(self, other: DocIdentity) -> bool:
        return bool(self.names and other.names and self.names.isdisjoint(other.names))


def conflicts(docs: dict[str, DocIdentity]) -> dict[str, dict[str, list[str]]]:
    """File -> {"dob": [files it disagrees with], "name": [...]}, for every file."""
    out: dict[str, dict[str, list[str]]] = {n: {"dob": [], "name": []} for n in docs}
    for (a, ia), (b, ib) in combinations(sorted(docs.items()), 2):
        if ia.dob_conflict(ib):
            out[a]["dob"].append(b)
            out[b]["dob"].append(a)
        if ia.name_conflict(ib):
            out[a]["name"].append(b)
            out[b]["name"].append(a)
    return out
