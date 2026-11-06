"""The study's de-identification policy (data/config/<study>/deid_policy.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adjud.deid.textmatch import find_all

# Categories whose values are worth propagating to every document of the case: once a
# label tells us "this is the patient's MRN", every other occurrence is PHI too.
PROPAGATE = frozenset(
    {
        "PATIENT_NAME",
        "NEXT_OF_KIN",
        "DOB",
        "MRN",
        "INSURANCE_NO",
        "ADDRESS",
        "PHONE",
        "EMAIL",
        "CASE_REFERENCE",
    }
)


@dataclass
class Pattern:
    category: str
    regex: re.Pattern[str]


@dataclass
class Policy:
    version: int
    date_policy: str
    must_redact: list[str]
    should_redact: list[str]
    allow_terms: list[str]
    allow_patterns: list[re.Pattern[str]]
    labels: dict[str, list[str]]
    patterns: list[Pattern]
    address_max_words: int
    postcode: re.Pattern[str]
    ner_entities: list[str]
    ner_min_score: float
    ner_languages: list[str]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path) -> Policy:
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if d.get("date_policy", "dob_only") != "dob_only":
            raise ValueError("only date_policy: dob_only is implemented in this POC")
        terms, wildcards = [], []
        for item in [*d.get("allow_list", []), *d.get("extra_allow", [])]:
            if "*" in item:
                wildcards.append(re.compile(re.escape(item).replace(r"\*", r"\S*")))
            else:
                terms.append(item)
        addr = d.get("address_segment", {})
        ner = d.get("ner", {})
        return cls(
            version=int(d.get("version", 1)),
            date_policy=d.get("date_policy", "dob_only"),
            must_redact=list(d.get("must_redact", [])),
            should_redact=list(d.get("should_redact", [])),
            allow_terms=terms,
            allow_patterns=wildcards,
            labels={k: list(v) for k, v in (d.get("labels") or {}).items()},
            patterns=[
                Pattern(p["category"], re.compile(p["regex"])) for p in d.get("patterns", [])
            ],
            address_max_words=int(addr.get("max_words", 8)),
            postcode=re.compile(addr.get("postcode_regex", r"(?!x)x")),
            ner_entities=list(ner.get("entities", [])),
            ner_min_score=float(ner.get("min_score", 0.5)),
            ner_languages=list(ner.get("languages", ["en"])),
            raw=d,
        )

    @classmethod
    def for_study(cls, config_dir: Path, study: str) -> Policy:
        return cls.load(Path(config_dir) / study / "deid_policy.yaml")

    def requirement(self, category: str) -> str:
        if category in self.must_redact:
            return "must_redact"
        if category in self.should_redact:
            return "should_redact"
        return "redact"

    def allowed_spans(self, text: str) -> list[tuple[int, int]]:
        spans = [s for term in self.allow_terms for s in find_all(text, term)]
        spans += [m.span() for p in self.allow_patterns for m in p.finditer(text)]
        return spans
