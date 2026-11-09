"""Flow 1 intake: import AE files, validate, evaluate eligibility,
create cases idempotently, detect source changes, pull related data and prior events,
flag possible duplicates and notify the site.

    pipe = IntakePipeline(data_root, config_dir, edc_dir)
    result = pipe.import_file("inbox/ae_export.csv")

The public interface matches the Flow 1 test kit (`data/flow1-testkit/README.md`).

Order of writes for every record, so a crash at any point is safe to re-run:
case files -> case events -> evaluation log (source of truth) -> index (cache).
If a crash leaves a case without its evaluation entry, the next import finds the case
by its key (FileCaseStore.find_case_by_key) instead of creating a second one.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock
from openpyxl import load_workbook

from adjud import rules
from adjud.edc import MockEdc
from adjud.events import Clock, EventDraft, canonical_json, sha256_bytes, utc_now
from adjud.notify import Outbox
from adjud.paths import DataPaths
from adjud.store import FileCaseStore, write_atomic

# Import template (spec.md §11.2).
REQUIRED_COLUMNS = (
    "study_id",
    "site_id",
    "subject_id",
    "ae_id",
    "ae_term",
    "onset_date",
    "serious",
    "seriousness_criteria",
    "last_modified",
)
OPTIONAL_COLUMNS = (
    "llt_code",
    "pt_code",
    "soc_code",
    "meddra_version",
    "end_date",
    "outcome",
    "severity",
    "relatedness_investigator",
)
REQUIRED_VALUES = (
    "study_id",
    "site_id",
    "subject_id",
    "ae_id",
    "ae_term",
    "onset_date",
    "serious",
    "last_modified",
)
CODED_UPPER = ("serious", "outcome", "severity", "relatedness_investigator", "seriousness_criteria")
DATE_COLUMNS = ("onset_date", "end_date")
OUTCOMES = frozenset(
    {"RECOVERED", "RECOVERING", "NOT_RECOVERED", "RECOVERED_WITH_SEQUELAE", "FATAL", "UNKNOWN"}
)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IMPORTABLE = (".csv", ".xlsx")

SYSTEM = "system:intake"


# --- results ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    file: str
    status: str = "ACCEPTED"  # ACCEPTED | REJECTED
    records: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    outcomes: list[dict[str, Any]] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(o["outcome"] == outcome for o in self.outcomes)

    @property
    def new_cases(self) -> int:
        return self.count("created")

    def summary(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "status": self.status,
            "records": self.records,
            "new_cases": self.new_cases,
            "changed": self.count("changed"),
            "unchanged": self.count("unchanged"),
            "stale": self.count("stale"),
            "not_eligible": self.count("not_eligible"),
            "errors": self.errors,
            "outcomes": self.outcomes,
        }


def _error(row: int | None, column: str | None, code: str, message: str = "") -> dict[str, Any]:
    return {"row": row, "column": column, "code": code, "message": message}


# --- reading files -----------------------------------------------------------------------


def _cell_text(value: Any, column: str) -> str:
    """Excel cells to text: dates as ISO, datetimes as ISO UTC, whole floats as ints."""
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        if column in DATE_COLUMNS:
            return value.date().isoformat()
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def read_table(path: Path) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """(header, rows with "_row" numbers counting the header as row 1, file errors)."""
    if path.suffix.lower() == ".xlsx":
        try:
            wb = load_workbook(path, data_only=True, read_only=True)
        except Exception as exc:  # noqa: BLE001 - any parse failure means "not a workbook"
            return [], [], [_error(None, None, "UNREADABLE_FILE", str(exc)[:200])]
        rows_iter = wb.worksheets[0].iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(rows_iter, ())]
        rows = []
        for n, values in enumerate(rows_iter, start=2):
            if values is None or all(v in (None, "") for v in values):
                continue
            rows.append(
                {"_row": n, **{h: _cell_text(v, h) for h, v in zip(header, values, strict=False)}}
            )
        wb.close()
        return header, rows, []
    try:
        text = path.read_bytes().decode("utf-8-sig")  # tolerate Excel's UTF-8 BOM
    except UnicodeDecodeError:
        return [], [], [_error(None, None, "INVALID_ENCODING", "file is not UTF-8")]
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=",")
    header = [h.strip() for h in (reader.fieldnames or [])]
    rows = [
        {"_row": n, **{(k or "").strip(): (v or "") for k, v in r.items()}}
        for n, r in enumerate(reader, start=2)
    ]
    return header, rows, []


def _is_date(v: str) -> bool:
    if not ISO_DATE.match(v):
        return False
    try:
        dt.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _parse_datetime(v: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def normalise(raw: dict[str, Any]) -> dict[str, str]:
    """Template columns only, trimmed; coded values upper-cased. Unicode is kept as is."""
    out = {c: str(raw.get(c, "") or "").strip() for c in REQUIRED_COLUMNS + OPTIONAL_COLUMNS}
    for c in CODED_UPPER:
        out[c] = out[c].upper()
    return out


def ae_key(r: dict[str, Any]) -> str:
    return f"{r['study_id']}|{r['subject_id']}|{r['ae_id']}"


# --- the pipeline ------------------------------------------------------------------------


class IntakePipeline:
    def __init__(
        self,
        store_root: Path | str,
        config_dir: Path | str,
        edc_dir: Path | str,
        study: str = "DEMO01",
        clock: Clock = utc_now,
    ):
        self.paths = DataPaths(Path(store_root))
        self.store = FileCaseStore(self.paths, clock=clock)
        self.study = study
        self.clock = clock
        cfg = Path(config_dir) / study
        self.study_dir = cfg
        self.rules = yaml.safe_load((cfg / "eligibility_rules.yaml").read_text(encoding="utf-8"))
        self.related_cfg = yaml.safe_load((cfg / "related_data.yaml").read_text(encoding="utf-8"))
        self.edc = MockEdc(Path(edc_dir))
        self.outbox = Outbox(self.paths, study, clock=clock)
        for folder in (self.paths.cases, self.paths.intake, self.paths.outbox):
            folder.mkdir(parents=True, exist_ok=True)

    def _lock(self) -> FileLock:
        """One import at a time across runner and UI."""
        return FileLock(self.paths.intake / ".import.lock")

    # --- validation -------------------------------------------------------------------

    def validate_row(self, r: dict[str, str], row: int | None) -> list[dict[str, Any]]:
        errs: list[dict[str, Any]] = []
        for c in REQUIRED_VALUES:
            if not r[c]:
                errs.append(_error(row, c, "MISSING_VALUE"))
        if r["study_id"] and r["study_id"] != self.study:
            errs.append(_error(row, "study_id", "WRONG_STUDY", f"expected {self.study}"))
        if r["subject_id"]:
            subject = self.edc.subject(r["subject_id"])
            if subject is None:
                errs.append(_error(row, "subject_id", "UNKNOWN_SUBJECT"))
            elif r["site_id"] and subject["site_id"] != r["site_id"]:
                errs.append(_error(row, "site_id", "SITE_MISMATCH"))
        for c in DATE_COLUMNS:
            if r[c] and not _is_date(r[c]):
                errs.append(_error(row, c, "INVALID_DATE", "expected YYYY-MM-DD"))
        if (
            _is_date(r["onset_date"])
            and _is_date(r["end_date"])
            and r["end_date"] < r["onset_date"]
        ):
            errs.append(_error(row, "end_date", "END_BEFORE_ONSET"))
        if r["serious"] and r["serious"] not in ("Y", "N"):
            errs.append(_error(row, "serious", "INVALID_VALUE", "expected Y or N"))
        if r["serious"] == "Y" and not r["seriousness_criteria"]:
            errs.append(
                _error(row, "seriousness_criteria", "MISSING_VALUE", "required when serious = Y")
            )
        if r["outcome"] and r["outcome"] not in OUTCOMES:
            errs.append(_error(row, "outcome", "INVALID_VALUE"))
        if r["last_modified"] and _parse_datetime(r["last_modified"]) is None:
            errs.append(_error(row, "last_modified", "INVALID_DATETIME"))
        return errs

    def validate(
        self, header: list[str], raw_rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            return [], [_error(1, c, "MISSING_COLUMN", "required column absent") for c in missing]
        rows, errors, seen = [], [], {}
        for raw in raw_rows:
            r = normalise(raw)
            errs = self.validate_row(r, raw["_row"])
            k = ae_key(r)
            if k in seen:
                errs.append(
                    _error(raw["_row"], "ae_id", "DUPLICATE_KEY", f"same AE as row {seen[k]}")
                )
            else:
                seen[k] = raw["_row"]
            errors += errs
            rows.append({**r, "_row": raw["_row"]})
        return rows, errors

    # --- public API -------------------------------------------------------------------

    def import_file(self, path: Path | str, actor: str = SYSTEM) -> ImportResult:
        """Import one AE file. An invalid file is rejected whole: nothing is logged."""
        path = Path(path)
        res = ImportResult(file=path.name)
        if path.suffix.lower() not in IMPORTABLE:
            res.status = "REJECTED"
            res.errors = [_error(None, None, "UNSUPPORTED_TYPE", "expected .csv or .xlsx")]
            return res
        header, raw_rows, file_errors = read_table(path)
        rows, errors = ([], file_errors) if file_errors else self.validate(header, raw_rows)
        if errors:
            res.status, res.errors = "REJECTED", errors
            return res
        res.records = len(rows)
        with self._lock():
            for r in rows:
                res.outcomes.append(self._process(r, source="FILE", origin=path.name, actor=actor))
        return res

    def manual_entry(self, record: dict[str, Any], reason: str, actor: str) -> ImportResult:
        """F1-08: an AE typed in by an authorised user, with a mandatory reason."""
        res = ImportResult(file="manual_entry")
        if not (reason or "").strip():
            res.status = "REJECTED"
            res.errors = [_error(None, "reason", "MISSING_REASON", "a reason is required")]
            return res
        r = normalise(record)
        errs = self.validate_row(r, None)
        if errs:
            res.status, res.errors = "REJECTED", errs
            return res
        res.records = 1
        with self._lock():
            res.outcomes.append(
                self._process(
                    {**r, "_row": None},
                    source="MANUAL",
                    origin="manual",
                    actor=actor,
                    reason=reason.strip(),
                )
            )
        return res

    def process_inbox(
        self, inbox: Path | str, processed: Path | str, actor: str = SYSTEM
    ) -> list[ImportResult]:
        """Runner entry point: import every file in the inbox, then move it to
        processed/ next to a JSON report. Existing files there are never overwritten."""
        inbox, processed = Path(inbox), Path(processed)
        processed.mkdir(parents=True, exist_ok=True)
        results = []
        for f in sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")):
            result = self.import_file(f, actor)
            dest = processed / f.name
            if dest.exists():
                stamp = self.clock().strftime("%Y%m%dT%H%M%S")
                dest = processed / f"{f.stem}.{stamp}{f.suffix}"
            shutil.move(str(f), dest)
            write_atomic(
                processed / f"{dest.name}.report.json",
                json.dumps(result.summary(), indent=2, ensure_ascii=False).encode(),
                overwrite=False,
            )
            results.append(result)
        return results

    def case_for(self, key: str) -> str | None:
        return self.store.load_index().get(key, {}).get("case_id")

    def case_state(self, case_id: str) -> dict[str, Any]:
        cs = self.store.state(case_id)
        return {"case_id": case_id, "state": cs.state.value, "flags": list(cs.flags)}

    # --- core -------------------------------------------------------------------------

    def _context(self, r: dict[str, str]) -> dict[str, Any]:
        """Rule context: the AE record merged with the subject's demographics."""
        return {**(self.edc.subject(r["subject_id"]) or {}), **r}

    def _event(self, case_id: str, type_: str, data: dict[str, Any], actor: str = SYSTEM,
               reason: str | None = None) -> None:  # fmt: skip
        self.store.append_event(
            case_id, EventDraft(type=type_, actor=actor, data=data, reason=reason)
        )

    def _notify(self, case_id: str, to: str, template: str) -> None:
        note = self.outbox.queue(case_id, to, template)
        self._event(
            case_id, "notification.queued", {"to": to, "template": template, "id": note["id"]}
        )

    def _snapshot_version(self, case_id: str) -> int:
        return 1 + sum(e["type"] == "source.changed" for e in self.store.events(case_id))

    def latest_snapshot(self, case_id: str) -> dict[str, Any]:
        rel = None
        for e in self.store.events(case_id):
            if e["type"] in ("case.created", "source.changed"):
                rel = e["data"]["snapshot"]["path"]
        assert rel is not None
        return self.store.get_json(case_id, rel)

    def _process(
        self,
        r: dict[str, Any],
        *,
        source: str,
        origin: str,
        actor: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        record = {k: v for k, v in r.items() if not k.startswith("_")}
        key = ae_key(record)
        rhash = sha256_bytes(canonical_json(record).encode())
        index = self.store.load_index()
        prev = index.get(key)
        if prev is None and (orphan := self.store.find_case_by_key(key)):
            prev = {"case_id": orphan, "last_modified": "", "hash": None, "eligible": True}
        case_id = prev.get("case_id") if prev else None
        out: dict[str, Any] = {"key": key, "row": r.get("_row"), "case_id": case_id}

        def log(outcome: str, ev: dict[str, Any] | None, cid: str | None) -> None:
            self.store.intake_log().append(
                {
                    "origin": origin,
                    "row": r.get("_row"),
                    "key": key,
                    "source": source,
                    "actor": actor,
                    "outcome": outcome,
                    "eligible": ev["eligible"] if ev else (prev or {}).get("eligible"),
                    "failed_rules": ev["failed_rules"] if ev else None,
                    "rule_set": self.rules.get("rule_set"),
                    "rule_version": self.rules.get("version"),
                    "last_modified": record["last_modified"],
                    "record_hash": rhash,
                    "case_id": cid,
                    "explanation": ev["results"] if ev else None,
                }
            )

        if prev and prev.get("hash"):
            new_time = _parse_datetime(record["last_modified"])
            old_time = _parse_datetime(prev["last_modified"])
            if new_time and old_time and new_time < old_time:
                if case_id:
                    self._event(
                        case_id,
                        "source.stale_ignored",
                        {"last_modified": record["last_modified"], "record_hash": rhash},
                        actor,
                    )
                log("stale", None, case_id)
                return {**out, "outcome": "stale", "eligible": prev.get("eligible")}
            if rhash == prev["hash"]:
                log("unchanged", None, case_id)
                return {**out, "outcome": "unchanged", "eligible": prev.get("eligible")}

        ev = rules.evaluate(self.rules, self._context(record))

        if case_id:  # existing case: store a new snapshot and flag the change
            flags = self._record_change(case_id, record, ev, actor)
            log("changed", ev, case_id)
            index[key] = {"case_id": case_id, "last_modified": record["last_modified"],
                          "hash": rhash, "eligible": ev["eligible"]}  # fmt: skip
            self.store.save_index(index)
            return {**out, "outcome": "changed", "eligible": ev["eligible"],
                    "failed_rules": ev["failed_rules"], "flags": flags}  # fmt: skip

        if not ev["eligible"]:
            log("not_eligible", ev, None)
            index[key] = {"case_id": None, "last_modified": record["last_modified"],
                          "hash": rhash, "eligible": False}  # fmt: skip
            self.store.save_index(index)
            return {**out, "outcome": "not_eligible", "eligible": False,
                    "failed_rules": ev["failed_rules"]}  # fmt: skip

        case_id = self._create_case(record, ev, source=source, origin=origin, actor=actor,
                                    reason=reason)  # fmt: skip
        log("created", ev, case_id)
        index[key] = {"case_id": case_id, "last_modified": record["last_modified"],
                      "hash": rhash, "eligible": True}  # fmt: skip
        self.store.save_index(index)
        return {**out, "case_id": case_id, "outcome": "created", "eligible": True,
                "failed_rules": [], "flags": self.case_state(case_id)["flags"]}  # fmt: skip

    def _record_change(
        self, case_id: str, record: dict[str, str], ev: dict[str, Any], actor: str
    ) -> list[str]:
        """F1-04: new snapshot, rules re-run, case flagged. Nothing is withdrawn."""
        n = self._snapshot_version(case_id) + 1
        snap_rel, elig_rel = f"source/ae_snapshot_v{n}.json", f"source/eligibility_v{n}.json"
        snap = self.store.put_json(case_id, snap_rel, record)
        elig = self.store.put_json(case_id, elig_rel, ev)
        self._event(
            case_id,
            "source.changed",
            {
                "snapshot": {"path": snap_rel, "sha256": snap},
                "eligibility": {"path": elig_rel, "sha256": elig},
                "eligible_now": ev["eligible"],
                "failed_rules": ev["failed_rules"],
            },
            actor,
        )
        flags = ["source_changed"]
        if not ev["eligible"]:
            self._event(case_id, "case.flagged", {"flag": "no_longer_eligible",
                                                  "failed_rules": ev["failed_rules"]})  # fmt: skip
            flags.append("no_longer_eligible")
        self._notify(case_id, "role:safety_specialist", "source_changed")
        return flags

    def _create_case(
        self,
        record: dict[str, str],
        ev: dict[str, Any],
        *,
        source: str,
        origin: str,
        actor: str,
        reason: str | None,
    ) -> str:
        s = self.store
        case_id = s.new_case_id(self.study)
        snap = s.put_json(case_id, "source/ae_snapshot_v1.json", record)
        elig = s.put_json(case_id, "source/eligibility_v1.json", ev)
        s.create_case(
            case_id,
            EventDraft(
                type="case.created",
                actor=actor,
                reason=reason,
                data={
                    "key": ae_key(record),
                    "study": record["study_id"],
                    "site": record["site_id"],
                    "subject": record["subject_id"],
                    "ae_id": record["ae_id"],
                    "source": source,
                    "origin": origin,
                    "snapshot": {"path": "source/ae_snapshot_v1.json", "sha256": snap},
                    "eligibility": {"path": "source/eligibility_v1.json", "sha256": elig},
                },
            ),
        )
        related = self.related_data(record)
        prior = self.prior_events(record)
        rel_sha = s.put_json(case_id, "source/related_data.json", related)
        prior_sha = s.put_json(case_id, "source/prior_events.json", prior)
        self._event(
            case_id,
            "edc.related_data_pulled",
            {
                "related_data": {"path": "source/related_data.json", "sha256": rel_sha},
                "prior_events": {"path": "source/prior_events.json", "sha256": prior_sha},
                "counts": {k: len(v) for k, v in related.items() if isinstance(v, list)},
                "prior_event_count": len(prior),
            },
        )
        if dup := self.duplicate_of(record, case_id):
            self._event(
                case_id,
                "case.flagged",
                {"flag": "possible_duplicate", "of_key": dup[0], "of_case": dup[1]},
            )
            self._notify(case_id, "role:safety_specialist", "possible_duplicate")
        self._notify(case_id, f"site:{record['site_id']}", "documents_requested")
        return case_id

    # --- EDC lookups ------------------------------------------------------------------

    def related_data(self, r: dict[str, str]) -> dict[str, Any]:
        """F1-06, filtered per related_data.yaml. Demographics hold no identifiers."""
        cfg, subject = self.related_cfg, r["subject_id"]
        onset = dt.date.fromisoformat(r["onset_date"])
        demo = self.edc.subject(subject) or {}
        out: dict[str, Any] = {
            "demographics": {f: demo.get(f, "") for f in cfg["demographics"]["fields"]}
        }
        history = [m for m in self.edc.medical_history if m["subject_id"] == subject]
        out["medical_history"] = history if cfg["medical_history"].get("all", True) else []
        conmeds = [c for c in self.edc.conmeds if c["subject_id"] == subject]
        if cfg["conmeds"].get("ongoing_at_onset", True):
            conmeds = [
                c
                for c in conmeds
                if dt.date.fromisoformat(c["start_date"]) <= onset
                and (not c["end_date"] or dt.date.fromisoformat(c["end_date"]) >= onset)
            ]
        out["conmeds"] = conmeds
        window = int(cfg["labs"]["window_days"])
        out["labs"] = [
            lab
            for lab in self.edc.labs
            if lab["subject_id"] == subject
            and lab["test"] in cfg["labs"]["tests"]
            and abs((dt.date.fromisoformat(lab["collected_date"]) - onset).days) <= window
        ]
        return out

    def prior_events(self, r: dict[str, str]) -> list[dict[str, Any]]:
        """F1-07: earlier AEs of the subject within the window (inclusive), strictly
        before onset, matched on the first configured code that agrees (PT, then SOC)."""
        cfg = self.related_cfg["prior_events"]
        onset = dt.date.fromisoformat(r["onset_date"])
        window = int(cfg["window_days"])
        labels = {"pt_code": "PT", "soc_code": "SOC"}
        out = []
        for a in self.edc.ae:
            if a["subject_id"] != r["subject_id"] or a["ae_id"] == r["ae_id"]:
                continue
            started = dt.date.fromisoformat(a["onset_date"])
            days = (onset - started).days
            if not (0 < days <= window):
                continue
            match = next(
                (labels.get(f, f) for f in cfg["match_on"] if r.get(f) and a.get(f) == r.get(f)),
                None,
            )
            if match:
                out.append(
                    {
                        "ae_id": a["ae_id"],
                        "ae_term": a["ae_term"],
                        "onset_date": a["onset_date"],
                        "match": match,
                        "days_before": days,
                    }
                )
        return sorted(out, key=lambda x: x["onset_date"])

    def duplicate_of(self, r: dict[str, str], own_case: str) -> tuple[str, str] | None:
        """F1-09: another case for the same subject and PT whose dates overlap or touch."""
        if not r["pt_code"]:
            return None
        s0 = dt.date.fromisoformat(r["onset_date"])
        e0 = dt.date.fromisoformat(r["end_date"] or r["onset_date"])
        for case_id in self.store.list_cases():
            if case_id == own_case:
                continue
            other = self.latest_snapshot(case_id)
            if other["subject_id"] != r["subject_id"] or other["pt_code"] != r["pt_code"]:
                continue
            s1 = dt.date.fromisoformat(other["onset_date"])
            e1 = dt.date.fromisoformat(other["end_date"] or other["onset_date"])
            if s0 <= e1 and s1 <= e0:
                return ae_key(other), case_id
        return None
