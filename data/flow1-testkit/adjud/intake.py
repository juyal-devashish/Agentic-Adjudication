"""Flow 1 intake: import AE files, validate, evaluate eligibility, create cases idempotently,
detect source changes, pull related data and prior events, queue notifications.

    pipe = IntakePipeline(store_root, kit_root / "config", kit_root / "edc_mock")
    result = pipe.import_file("inbox_scenarios/01_initial.csv")
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from openpyxl import load_workbook

from . import rules
from .store import FileStore, canonical, sha256_bytes

REQUIRED_COLUMNS = ["study_id", "site_id", "subject_id", "ae_id", "ae_term", "onset_date", "serious",
                    "seriousness_criteria", "last_modified"]
OPTIONAL_COLUMNS = ["llt_code", "pt_code", "soc_code", "meddra_version", "end_date", "outcome", "severity",
                    "relatedness_investigator"]
REQUIRED_VALUES = ["study_id", "site_id", "subject_id", "ae_id", "ae_term", "onset_date", "serious", "last_modified"]
OUTCOMES = {"RECOVERED", "RECOVERING", "NOT_RECOVERED", "RECOVERED_WITH_SEQUELAE", "FATAL", "UNKNOWN"}
UPPER = ("serious", "outcome", "severity", "relatedness_investigator", "seriousness_criteria")
DATE_COLUMNS = ("onset_date", "end_date")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ImportResult:
    file: str
    status: str = "ACCEPTED"                      # ACCEPTED | REJECTED
    records: int = 0
    errors: list[dict] = field(default_factory=list)
    outcomes: list[dict] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(o["outcome"] == outcome for o in self.outcomes)

    @property
    def new_cases(self) -> int:
        return self.count("created")

    def summary(self) -> dict:
        return {"file": self.file, "status": self.status, "records": self.records,
                "new_cases": self.new_cases, "changed": self.count("changed"),
                "unchanged": self.count("unchanged"), "stale": self.count("stale"),
                "not_eligible": self.count("not_eligible"), "errors": self.errors}


def _iso(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def _date_ok(v: str) -> bool:
    if not ISO_DATE.match(v):
        return False
    try:
        dt.date.fromisoformat(v); return True
    except ValueError:
        return False


def _datetime_ok(v: str) -> bool:
    try:
        dt.datetime.fromisoformat(v.replace("Z", "+00:00")); return True
    except ValueError:
        return False


class MockEdc:
    """Reads the CSV files that stand in for the EDC."""

    def __init__(self, edc_dir: Path):
        def load(name):
            with open(edc_dir / name, encoding="utf-8") as f:
                return list(csv.DictReader(f))
        self.demographics = {r["subject_id"]: r for r in load("demographics.csv")}
        self.medical_history = load("medical_history.csv")
        self.conmeds = load("conmeds.csv")
        self.labs = load("labs.csv")
        self.ae = load("ae.csv")


class IntakePipeline:
    def __init__(self, store_root, config_dir, edc_dir, study: str = "DEMO01"):
        self.store = FileStore(store_root)
        self.study = study
        cfg = Path(config_dir) / study
        self.rules = yaml.safe_load((cfg / "eligibility_rules.yaml").read_text(encoding="utf-8"))
        self.related_cfg = yaml.safe_load((cfg / "related_data.yaml").read_text(encoding="utf-8"))
        self.edc = MockEdc(Path(edc_dir))

    # ------------------------------------------------------------------ reading files
    def _read(self, path: Path) -> tuple[list[str], list[dict], list[dict]]:
        """Return (header, rows with '_row' numbers, file-level errors)."""
        if path.suffix.lower() == ".xlsx":
            wb = load_workbook(path, data_only=True, read_only=True)
            ws = wb.worksheets[0]
            it = ws.iter_rows(values_only=True)
            header = [str(h).strip() if h is not None else "" for h in next(it, [])]
            rows = []
            for n, vals in enumerate(it, start=2):
                if vals is None or all(v in (None, "") for v in vals):
                    continue
                rows.append({"_row": n, **{h: _iso(v.date() if h in DATE_COLUMNS and isinstance(v, dt.datetime) else v)
                                           for h, v in zip(header, vals)}})
            return header, rows, []
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return [], [], [{"row": None, "column": None, "code": "INVALID_ENCODING", "message": "file is not UTF-8"}]
        reader = csv.DictReader(text.splitlines(), delimiter=",")
        header = [h.strip() for h in (reader.fieldnames or [])]
        rows = [{"_row": n, **{(k or "").strip(): (v or "") for k, v in r.items()}}
                for n, r in enumerate(reader, start=2)]
        return header, rows, []

    # ------------------------------------------------------------------ validation
    def _normalise(self, r: dict) -> dict:
        out = {c: str(r.get(c, "") or "").strip() for c in REQUIRED_COLUMNS + OPTIONAL_COLUMNS}
        for c in UPPER:
            out[c] = out[c].upper()
        return out

    def _validate_row(self, r: dict, row_no) -> list[dict]:
        errs = []
        def err(col, code, msg=""):
            errs.append({"row": row_no, "column": col, "code": code, "message": msg})
        for c in REQUIRED_VALUES:
            if not r[c]:
                err(c, "MISSING_VALUE")
        if r["study_id"] and r["study_id"] != self.study:
            err("study_id", "WRONG_STUDY", f"expected {self.study}")
        if r["subject_id"]:
            subj = self.edc.demographics.get(r["subject_id"])
            if subj is None:
                err("subject_id", "UNKNOWN_SUBJECT")
            elif r["site_id"] and subj["site_id"] != r["site_id"]:
                err("site_id", "SITE_MISMATCH")
        for c in ("onset_date", "end_date"):
            if r[c] and not _date_ok(r[c]):
                err(c, "INVALID_DATE", "expected YYYY-MM-DD")
        if r["onset_date"] and r["end_date"] and _date_ok(r["onset_date"]) and _date_ok(r["end_date"]) \
                and r["end_date"] < r["onset_date"]:
            err("end_date", "END_BEFORE_ONSET")
        if r["serious"] and r["serious"] not in ("Y", "N"):
            err("serious", "INVALID_VALUE", "expected Y or N")
        if r["serious"] == "Y" and not r["seriousness_criteria"]:
            err("seriousness_criteria", "MISSING_VALUE", "required when serious = Y")
        if r["outcome"] and r["outcome"] not in OUTCOMES:
            err("outcome", "INVALID_VALUE")
        if r["last_modified"] and not _datetime_ok(r["last_modified"]):
            err("last_modified", "INVALID_DATETIME")
        return errs

    def _validate(self, header, raw_rows) -> tuple[list[dict], list[dict]]:
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            return [], [{"row": 1, "column": c, "code": "MISSING_COLUMN", "message": "required column absent"} for c in missing]
        errors, rows, seen = [], [], {}
        for raw in raw_rows:
            r = self._normalise(raw)
            errs = self._validate_row(r, raw["_row"])
            k = self.key(r)
            if k in seen:
                errs.append({"row": raw["_row"], "column": "ae_id", "code": "DUPLICATE_KEY",
                             "message": f"same study/subject/ae_id as row {seen[k]}"})
            else:
                seen[k] = raw["_row"]
            errors += errs
            rows.append({**r, "_row": raw["_row"]})
        return rows, errors

    @staticmethod
    def key(r: dict) -> str:
        return f"{r['study_id']}|{r['subject_id']}|{r['ae_id']}"

    # ------------------------------------------------------------------ public API
    def import_file(self, path, actor: str = "system") -> ImportResult:
        path = Path(path)
        res = ImportResult(file=path.name)
        header, raw_rows, file_errors = self._read(path)
        rows, errors = ([], file_errors) if file_errors else self._validate(header, raw_rows)
        if errors:
            res.status, res.errors = "REJECTED", errors
            return res
        res.records = len(rows)
        for r in rows:
            res.outcomes.append(self._process(r, source="FILE", origin=path.name, actor=actor))
        return res

    def manual_entry(self, record: dict, reason: str, actor: str) -> ImportResult:
        res = ImportResult(file="manual_entry")
        if not (reason or "").strip():
            res.status = "REJECTED"
            res.errors = [{"row": None, "column": "reason", "code": "MISSING_REASON", "message": "a reason is required"}]
            return res
        r = self._normalise(record)
        errs = self._validate_row(r, None)
        if errs:
            res.status, res.errors = "REJECTED", errs
            return res
        res.records = 1
        res.outcomes.append(self._process({**r, "_row": None}, source="MANUAL", origin="manual", actor=actor, reason=reason))
        return res

    def process_inbox(self, inbox: Path, processed: Path, actor: str = "system") -> list[ImportResult]:
        """Runner entry point: import every file in the inbox, then move it with a report."""
        results = []
        for f in sorted(Path(inbox).iterdir()):
            if f.is_file() and f.suffix.lower() in (".csv", ".xlsx"):
                r = self.import_file(f, actor)
                processed.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), processed / f.name)
                (processed / f"{f.name}.report.json").write_text(json.dumps(r.summary(), indent=2), encoding="utf-8")
                results.append(r)
        return results

    def case_for(self, key: str) -> str | None:
        return self.store.load_index().get(key, {}).get("case_id")

    def case_state(self, case_id: str) -> dict:
        """Replay a case's events into its current state and flags."""
        state, flags = None, []
        for e in self.store.events(case_id):
            t = e["type"]
            if t == "case.created":
                state = "EVENT_CREATED"
            elif t == "edc.related_data_pulled":
                state = "AWAITING_DOCUMENTS"
            elif t == "source.changed":
                flags.append("source_changed")
                if not e["data"]["eligible_now"]:
                    flags.append("no_longer_eligible")
            elif t == "case.flagged":
                flags.append(e["data"]["flag"])
            elif t == "case.withdrawn":
                state = "WITHDRAWN"
        return {"case_id": case_id, "state": state, "flags": flags}

    # ------------------------------------------------------------------ core
    def _context(self, r: dict) -> dict:
        demo = self.edc.demographics.get(r["subject_id"], {})
        return {**demo, **r}

    def _process(self, r: dict, *, source: str, origin: str, actor: str, reason: str | None = None) -> dict:
        record = {k: v for k, v in r.items() if not k.startswith("_")}
        k = self.key(record)
        rhash = sha256_bytes(canonical(record))
        index = self.store.load_index()
        prev = index.get(k)
        out = {"key": k, "row": r.get("_row"), "case_id": prev.get("case_id") if prev else None}

        def log(outcome, ev=None, case_id=None):
            self.store.log_evaluation({
                "origin": origin, "row": r.get("_row"), "key": k, "source": source, "actor": actor,
                "outcome": outcome, "eligible": ev["eligible"] if ev else (prev or {}).get("eligible"),
                "failed_rules": ev["failed_rules"] if ev else None,
                "rule_set": self.rules.get("rule_set"), "rule_version": self.rules.get("version"),
                "last_modified": record["last_modified"], "record_hash": rhash,
                "case_id": case_id or out["case_id"], "explanation": ev["results"] if ev else None})

        if prev:
            if record["last_modified"] < prev["last_modified"]:
                log("stale")
                if prev.get("case_id"):
                    self.store.append_event(prev["case_id"], "source.stale_ignored",
                                            {"last_modified": record["last_modified"], "record_hash": rhash}, actor)
                return {**out, "outcome": "stale", "eligible": prev.get("eligible")}
            if rhash == prev["hash"]:
                log("unchanged")
                return {**out, "outcome": "unchanged", "eligible": prev.get("eligible")}

        ev = rules.evaluate(self.rules, self._context(record))
        if prev and prev.get("case_id"):
            cid = prev["case_id"]
            n = len([e for e in self.store.events(cid) if e["type"] in ("case.created", "source.changed")]) + 1
            snap = self.store.put_json(cid, f"source/ae_snapshot_v{n}.json", record)
            esha = self.store.put_json(cid, f"source/eligibility_v{n}.json", ev)
            self.store.append_event(cid, "source.changed", {"snapshot": f"source/ae_snapshot_v{n}.json", "sha256": snap,
                                                            "eligibility_sha256": esha, "eligible_now": ev["eligible"],
                                                            "failed_rules": ev["failed_rules"]}, actor)
            self.store.queue_notification(cid, "role:safety_specialist", "source_changed")
            index[k] = {**prev, "last_modified": record["last_modified"], "hash": rhash, "eligible": ev["eligible"]}
            self.store.save_index(index)
            log("changed", ev, cid)
            return {**out, "outcome": "changed", "eligible": ev["eligible"], "failed_rules": ev["failed_rules"],
                    "flags": ["source_changed"] + ([] if ev["eligible"] else ["no_longer_eligible"])}

        if not ev["eligible"]:
            index[k] = {"case_id": None, "last_modified": record["last_modified"], "hash": rhash, "eligible": False}
            self.store.save_index(index)
            log("not_eligible", ev)
            return {**out, "outcome": "not_eligible", "eligible": False, "failed_rules": ev["failed_rules"]}

        cid = self._create_case(record, ev, source, origin, actor, reason)
        index[k] = {"case_id": cid, "last_modified": record["last_modified"], "hash": rhash, "eligible": True}
        self.store.save_index(index)
        log("created", ev, cid)
        flags = [e["data"]["flag"] for e in self.store.events(cid) if e["type"] == "case.flagged"]
        return {**out, "case_id": cid, "outcome": "created", "eligible": True, "failed_rules": [], "flags": flags}

    def _create_case(self, record, ev, source, origin, actor, reason) -> str:
        s = self.store
        cid = s.new_case_id(self.study)
        snap = s.put_json(cid, "source/ae_snapshot_v1.json", record)
        esha = s.put_json(cid, "source/eligibility_v1.json", ev)
        s.append_event(cid, "case.created", {"key": self.key(record), "source": source, "origin": origin,
                                             "snapshot": "source/ae_snapshot_v1.json", "sha256": snap,
                                             "eligibility_sha256": esha}, actor, reason)
        related = self.related_data(record)
        prior = self.prior_events(record)
        rsha = s.put_json(cid, "source/related_data.json", related)
        psha = s.put_json(cid, "source/prior_events.json", prior)
        s.append_event(cid, "edc.related_data_pulled", {"sha256": rsha, "prior_events_sha256": psha,
                                                        "counts": {k: len(v) for k, v in related.items() if isinstance(v, list)},
                                                        "prior_events": len(prior)}, "system")
        dup = self.duplicate_of(record, cid)
        if dup:
            s.append_event(cid, "case.flagged", {"flag": "possible_duplicate", "of_key": dup[0], "of_case": dup[1]}, "system")
            s.queue_notification(cid, "role:safety_specialist", "possible_duplicate")
        s.queue_notification(cid, f"site:{record['site_id']}", "documents_requested")
        s.append_event(cid, "notification.queued", {"to": f"site:{record['site_id']}", "template": "documents_requested"}, "system")
        return cid

    # ------------------------------------------------------------------ EDC lookups
    def related_data(self, r: dict) -> dict:
        cfg, subj = self.related_cfg, r["subject_id"]
        onset = dt.date.fromisoformat(r["onset_date"])
        demo = self.edc.demographics.get(subj, {})
        out = {"demographics": {f: demo.get(f, "") for f in cfg["demographics"]["fields"]}}
        out["medical_history"] = [m for m in self.edc.medical_history if m["subject_id"] == subj]
        out["conmeds"] = [c for c in self.edc.conmeds if c["subject_id"] == subj
                          and dt.date.fromisoformat(c["start_date"]) <= onset
                          and (not c["end_date"] or dt.date.fromisoformat(c["end_date"]) >= onset)]
        w = int(cfg["labs"]["window_days"])
        out["labs"] = [l for l in self.edc.labs if l["subject_id"] == subj and l["test"] in cfg["labs"]["tests"]
                       and abs((dt.date.fromisoformat(l["collected_date"]) - onset).days) <= w]
        return out

    def prior_events(self, r: dict) -> list[dict]:
        cfg = self.related_cfg["prior_events"]
        onset = dt.date.fromisoformat(r["onset_date"])
        out = []
        for a in self.edc.ae:
            if a["subject_id"] != r["subject_id"] or a["ae_id"] == r["ae_id"]:
                continue
            d = dt.date.fromisoformat(a["onset_date"])
            if not (d < onset and (onset - d).days <= int(cfg["window_days"])):
                continue
            match = "PT" if r["pt_code"] and a["pt_code"] == r["pt_code"] else \
                    "SOC" if r["soc_code"] and a["soc_code"] == r["soc_code"] else None
            if match:
                out.append({"ae_id": a["ae_id"], "ae_term": a["ae_term"], "onset_date": a["onset_date"],
                            "match": match, "days_before": (onset - d).days})
        return sorted(out, key=lambda x: x["onset_date"])

    def duplicate_of(self, r: dict, own_case: str) -> tuple[str, str] | None:
        """Another case for the same subject and PT whose date range overlaps or touches this one."""
        if not r["pt_code"]:
            return None
        s0 = dt.date.fromisoformat(r["onset_date"]); e0 = dt.date.fromisoformat(r["end_date"] or r["onset_date"])
        for cid in self.store.list_cases():
            if cid == own_case:
                continue
            o = self.store.get_json(cid, "source/ae_snapshot_v1.json")
            if o["subject_id"] != r["subject_id"] or o["pt_code"] != r["pt_code"]:
                continue
            s1 = dt.date.fromisoformat(o["onset_date"]); e1 = dt.date.fromisoformat(o["end_date"] or o["onset_date"])
            if s0 <= e1 and s1 <= e0:
                return self.key(o), cid
        return None
