"""The PHI gate on everything the Slack Agent posts (plan P-58, Q17, ADR 0005).

Anything without PHI may go to Slack; PHI is what de-identification removes under the
study's `deid_policy.yaml`. So before a text is posted, it is checked the way a redacted
output is verified (`adjud.deid.pdf.verify_pdf`): every detector (labels, patterns,
address segments, Presidio NER) on the text, then the generator's seeded identifiers
(synthetic data only, from `study.yaml: seeded_identities`) and the terms the site added.
For the cases the turn is about, every seeded value of the subject is searched as
verification searches it; for every other subject too, except that a bare surname
counts only in Title or UPPER case ("Ward", not "the ward"). As in verification, any
finding of any category holds the text back; nothing is dropped to make answers pass.
De-identification's learned values are not stored anywhere, so the seeded values stand
in for "the case's known values".

One difference: the people of the app are not patients. The names in `users.yaml` (with
and without their title), their institutions, the study's site names and the role names
are allowed, and so are the few ordinary words in `study.yaml: slack_phi_gate.allow`
that NER takes for a name ("Discharged home ..."). All by span: a finding is cut where
it overlaps one, and what is left of it still holds the text ("Anna Bell" minus the
user name "Anna" leaves "Bell"). Seeded and site-added values, and names after a patient
or next-of-kin label ("Patient: Tom Weber"), are never cut: a patient named like a user
is still caught.

A text the gate cannot check (a detector raised) is held too. Verdicts carry categories
and counts only, never the text found.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from adjud.deid.detect import HONORIFIC_WORDS, Finding, KnownValues, _subtract, detect
from adjud.deid.pagetext import TextUnit
from adjud.deid.policy import Policy
from adjud.deid.textmatch import find_all
from adjud.documents import documents_of, seeded_values
from adjud.slack_app.core import ROLE_WORDS, Workspace

log = logging.getLogger("adjud.slack_app")
HOLD_TEXT = "That answer may contain identifiers; open it in the app."
# Slack mrkdwn marks, read as spaces: `Smith_John.pdf` is checked as "Smith John.pdf"
_MARKS = str.maketrans(dict.fromkeys("*_`~", " "))
PATIENT_LABELS = frozenset({"PATIENT_NAME", "NEXT_OF_KIN"})
APP_WORDS = ("Quality Check", "Quality check", "AI Suggestion", "Site Query", "Adjudication Demo")


@dataclass(frozen=True)
class Verdict:
    held: bool
    found: dict[str, int] = field(default_factory=dict)  # category -> count, never the text
    error: str | None = None  # the gate could not check the text (it is then held)

    @property
    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"found": self.found}
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class PhiGate:
    ws: Workspace
    policy: Policy
    identities: dict[str, Any]
    allowed: list[str]

    @classmethod
    def for_workspace(cls, ws: Workspace) -> PhiGate:
        import json

        import yaml

        policy = Policy.for_study(ws.paths.config, ws.study)
        cfg = yaml.safe_load((ws.paths.config / ws.study / "study.yaml").read_text()) or {}
        rel = cfg.get("seeded_identities")  # synthetic data only
        path = ws.paths.root / rel if rel else None
        identities = json.loads(path.read_text())["subjects"] if path and path.exists() else {}
        if rel and not identities:  # e.g. a demo folder without the kit: detectors only
            log.warning("seeded_identities %s not found in %s: the PHI gate runs on its "
                        "detectors only", rel, ws.paths.root)  # fmt: skip
        extra = [str(w) for w in (cfg.get("slack_phi_gate") or {}).get("allow") or []]
        return cls(ws, policy, identities, allowed_terms(ws) + extra)

    def check(self, text: str, case_ids: Iterable[str] = ()) -> Verdict:
        """Hold the text if any identifier is found in it."""
        try:
            found = self.findings(text, case_ids)
        except Exception as exc:  # a text that cannot be checked is not posted
            log.exception("the PHI gate could not check a text")
            return Verdict(True, {}, type(exc).__name__)
        counts = Counter(f.category for f in found)
        return Verdict(bool(found), dict(sorted(counts.items())))

    def findings(self, text: str, case_ids: Iterable[str] = ()) -> list[Finding]:
        unit = TextUnit.from_string(text.translate(_MARKS))
        allowed = [span for term in self.allowed for span in find_all(unit.text, term)]
        found = [piece for f in detect(unit, self.policy, KnownValues(), language="en")
                 for piece in (_subtract(f, allowed, unit.text)
                               if allowed and not _labelled_patient(f) else [f])]  # fmt: skip
        for value, category in self._case_values(case_ids):
            found += [Finding(s, e, category, "seeded", unit.text[s:e])
                      for s, e in find_all(unit.text, value)]  # fmt: skip
        for surname in self._other_surnames(case_ids):  # Title or UPPER case only
            pattern = rf"(?<!\w)(?:{re.escape(surname)}|{re.escape(surname.upper())})(?!\w)"
            found += [Finding(m.start(), m.end(), "SEEDED", "seeded", m.group())
                      for m in re.finditer(pattern, unit.text)]  # fmt: skip
        return found

    def _subjects(self, case_ids: Iterable[str]) -> set[str]:
        cases = set(self.ws.store.list_cases())
        return {self.ws.store.state(c).subject or "" for c in set(case_ids) & cases}

    def _case_values(self, case_ids: Iterable[str]) -> list[tuple[str, str]]:
        """Every seeded identifier of the turn's subjects, as verification searches them
        (case-folded, the bare surname too) and the terms their sites added; for every
        other seeded subject, all but the bare surname, which as a case-folded word
        would also hit "the ward" or "troponin rose"."""
        case_ids = set(case_ids)
        in_turn = self._subjects(case_ids)
        out: list[tuple[str, str]] = []
        for subject, s in self.identities.items():
            values = seeded_values(self.identities, subject)
            if subject not in in_turn:
                values = [v for v in values if v != s.get("last")]
            out += [(v, "SEEDED") for v in values]
        for cid in sorted(case_ids & set(self.ws.store.list_cases())):
            out += [(t.strip(), "SITE_TERM") for t in documents_of(self.ws.store, cid).extra_terms
                    if t.strip()]  # fmt: skip
        return out

    def _other_surnames(self, case_ids: Iterable[str]) -> list[str]:
        in_turn = self._subjects(case_ids)
        names = {str(s.get("last") or "") for subject, s in self.identities.items()
                 if subject not in in_turn}  # fmt: skip
        return sorted(n for n in names if len(n) >= 3)


def _labelled_patient(f: Finding) -> bool:
    """A name after a patient or next-of-kin label ("Patient: Tom Weber") is never cut
    by the users' names: a user's name words are allowed in prose, not as a patient's."""
    return f.detector == "label" and f.category in PATIENT_LABELS


def allowed_terms(ws: Workspace) -> list[str]:
    """The app's people (each name with and without its title, the bare surname and
    first name, "Dr. Surname"), their institutions, site names and role words."""
    terms: set[str] = set(APP_WORDS) | set(ROLE_WORDS.values())
    terms |= {v for v in ws.people.site_names.values() if v}
    for u in ws.people.users:
        if u.get("institution"):
            terms.add(u["institution"])
        name = str(u.get("name") or "").strip()
        if not name:
            continue
        words = name.split()
        bare = [w for w in words if w.strip(".") not in HONORIFIC_WORDS]
        terms |= {name, " ".join(bare)}
        terms |= {w for w in bare if len(w) >= 3}
        for title in ("Dr.", "Dr"):  # titled forms, so no "Dr" is left over from a cut
            terms |= {f"{title} {' '.join(bare)}", f"{title} {bare[-1]}"}
    return sorted(t for t in terms if t and not re.fullmatch(r"\W*", t))
