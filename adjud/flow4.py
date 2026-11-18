"""Flow 4: outcome, export and close; re-adjudication (spec §6.7, §6.8; plan P-50, P-51).

Automatic steps, run by the runner:
    derive_outcome  OUTCOME → EXPORT: each final value from the signed answers it came
                    from (agreed, Adjudicator 3 or committee), with the derivation version;
                    a "related" outcome is e-mailed to pharmacovigilance (no clinical content)
    export          the outcome as a CSV for the EDC, in exports/edc/ (P-50: file only)
    acknowledge     the mock EDC accepts the file and writes its acknowledgement →
                    CLOSED, and a completion e-mail goes out

Human action:
    start_readjudication   the CEC Coordinator, signed, with a reason: CLOSED →
                           READY_FOR_ADJUDICATION, round n+1 with the same adjudicators.
                           Nothing from earlier rounds changes; their outcome is superseded.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adjud.events import EventDraft, format_time, sha256_bytes
from adjud.flow3 import SLOTS, Flow3, current_round
from adjud.forms import FormRefused
from adjud.signatures import SignatureRefused, load_users, sign
from adjud.store import FileCaseStore, json_bytes, write_atomic
from adjud.workflow import State, replay

SYSTEM_OUTCOME = "system:outcome"
SYSTEM_EDC = "system:edc-mock"
DERIVATION = "outcome-v1"
READJ_MEANING = "Re-adjudication started"
DECIDING = ("comparison.agreed", "adj3.resolved", "consensus.signed")


class Flow4Refused(Exception):
    pass


def _since_last_assignment(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = max(i for i, e in enumerate(events) if e["type"] == "adjudication.assigned")
    return events[start:]


def outcomes(store: FileCaseStore, case_id: str) -> list[dict[str, Any]]:
    """Every outcome of the case, oldest first; all but the latest are superseded (RDJ-03)."""
    found = [e for e in store.events(case_id) if e["type"] == "outcome.derived"]
    return [{**e["data"], "at": e["at"], "seq": e["seq"], "superseded": i < len(found) - 1}
            for i, e in enumerate(found)]  # fmt: skip


@dataclass
class Flow4:
    store: FileCaseStore
    config_dir: Path
    study: str = "DEMO01"
    flow3: Flow3 = field(init=False)

    def __post_init__(self) -> None:
        self.flow3 = Flow3(self.store, self.config_dir, self.study)

    def _event(self, case_id: str, type_: str, actor: str, data: dict[str, Any],
               expect: Callable[[list[dict[str, Any]]], None] | None = None,
               **kw: Any) -> None:  # fmt: skip
        """Append one event; `expect(prior events)` re-checks under the case lock."""
        self.store.append_event(case_id, EventDraft(type=type_, actor=actor, data=data, **kw),
                                expect=expect)  # fmt: skip

    # -- outcome -------------------------------------------------------------------------------

    def derive(self, case_id: str) -> dict[str, Any]:
        """The final values of this round and where each came from (OUT-01, OUT-02).
        Agreed fields take Adjudicator 1's answer (within a date tolerance the two may
        differ by the tolerance); a field Adjudicator 3 resolved takes Adjudicator 3's
        answer; the committee's fields take the signed consensus. Free-text fields are
        kept as each signer's note."""
        rnd = current_round(self.store, case_id)
        form = self.flow3.form(case_id)
        events = _since_last_assignment(self.store.events(case_id))
        signed = {e["data"]["slot"]: e for e in events
                  if e["type"] in ("adjudication.signed", "adj3.signed")}  # fmt: skip

        def source(slot: str, f: str) -> dict[str, Any]:
            e = signed[slot]
            return {"event_seq": e["seq"], "signer": e["signature"]["signer"],
                    "role": "Adjudicator " + slot, "field": f,
                    "answers": e["data"]["answers"]}  # fmt: skip

        answers = {s: self.store.get_json(case_id, e["data"]["answers"]["path"])
                   for s, e in signed.items()}  # fmt: skip
        values: dict[str, Any] = {}
        compared = next(e for e in events if e["type"].startswith("comparison."))
        for row in self.store.get_json(case_id, compared["data"]["comparison"]["path"]):
            if row["agree"]:
                f = row["field"]
                values[f] = {"value": row["1"], "how": "agreed",
                             "sources": [source(s, f) for s in SLOTS]}  # fmt: skip
        adj3 = next((e for e in events if e["type"] in ("adj3.resolved", "adj3.unresolved")), None)
        if adj3:
            for row in self.store.get_json(case_id, adj3["data"]["result"]["path"]):
                if row["matches"]:
                    f = row["field"]
                    both = [source("3", f), source(row["matches"], f)]
                    values[f] = {"value": row["3"], "how": "adjudicator_3", "sources": both}
        consensus = next((e for e in reversed(events) if e["type"] == "consensus.signed"), None)
        if consensus:
            meeting = self.store.get_json(case_id, consensus["data"]["meeting"]["path"])
            for f, v in meeting["decisions"].items():
                values[f] = {"value": v, "how": "committee",
                             "sources": [{"event_seq": consensus["seq"],
                                          "signer": consensus["signature"]["signer"],
                                          "role": "Committee Chair", "field": f,
                                          "meeting": consensus["data"]["meeting"]}]}  # fmt: skip
        missing = [f for f in form.compared_ids if f not in values]
        if missing:
            raise Flow4Refused(f"no final value for {', '.join(missing)}")
        try:  # the combined outcome must be a valid form: no confirmed MI without its type
            form.validate({f: v["value"] for f, v in values.items()}, form.compared_ids)
        except FormRefused as exc:
            raise Flow4Refused(f"the outcome is incomplete: {exc}") from exc
        notes = {f.id: {f"Adjudicator {s}": a.get(f.id) for s, a in sorted(answers.items())
                        if a.get(f.id)} for f in form.fields if not f.compared}  # fmt: skip
        related = self.flow3.cfg.get("related") or {}
        return {
            "case_id": case_id,
            "round": rnd.number,
            "form": rnd.form,
            "derivation": DERIVATION,
            "values": {
                f: {**v, "label": form.field(f).label, "words": form.field(f).words(v["value"])}
                for f, v in values.items()
            },  # fmt: skip
            "notes": notes,
            "related": bool(related)
            and values.get(related.get("field"), {}).get("value") == related.get("value"),
        }

    def derive_outcome(self, case_id: str) -> dict[str, Any]:
        outcome = self.derive(case_id)
        prior = [o for o in outcomes(self.store, case_id) if o["round"] < outcome["round"]]
        rel = f"outcome/outcome_round{outcome['round']}.json"
        sha = self.store.put_json_once(case_id, rel, outcome)
        self._event(case_id, "outcome.derived", SYSTEM_OUTCOME,
                    {"round": outcome["round"], "outcome": {"path": rel, "sha256": sha},
                     "derivation": DERIVATION, "related": outcome["related"],
                     "corrects": prior[-1]["outcome"] if prior else None})  # fmt: skip
        if outcome["related"]:  # OUT-03: the mailbox, never the case's clinical content
            self.flow3._notify(case_id, "mailbox:pharmacovigilance", "outcome_for_pv",
                               SYSTEM_OUTCOME)  # fmt: skip
        return outcome

    # -- export and close -----------------------------------------------------------------------

    def _latest(self, case_id: str, type_: str) -> dict[str, Any] | None:
        return next((e for e in reversed(self.store.events(case_id)) if e["type"] == type_), None)

    def export(self, case_id: str) -> Path:
        """The outcome as one CSV row per field (OUT-06, file only). A re-adjudication's
        file says it corrects the earlier export (RDJ-04)."""
        derived = self._latest(case_id, "outcome.derived")
        outcome = self.store.get_json(case_id, derived["data"]["outcome"]["path"])
        cs = self.store.state(case_id)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["study", "case_id", "subject_id", "ae_id", "round", "correction",
                    "field", "value", "decided_by", "signed_by", "derivation"])  # fmt: skip
        for f, v in outcome["values"].items():
            signers = "; ".join(s["signer"] for s in v["sources"])
            w.writerow([cs.study, case_id, cs.subject, cs.ae_id, outcome["round"],
                        "Y" if derived["data"]["corrects"] else "N", f, v["value"], v["how"],
                        signers, outcome["derivation"]])  # fmt: skip
        content = buf.getvalue().encode()
        name = f"{case_id}_round{outcome['round']}.csv"
        rel = f"export/{name}"
        target = self.store.paths.edc_exports / name
        path = self.store.case_dir(case_id) / rel
        sha = sha256_bytes(content)
        if not (path.exists() and path.read_bytes() == content):
            self.store.put_file(case_id, rel, content)
        if not (target.exists() and target.read_bytes() == content):
            write_atomic(target, content, overwrite=False)
        self._event(case_id, "export.sent", SYSTEM_OUTCOME,
                    {"file": {"path": rel, "sha256": sha}, "round": outcome["round"],
                     "to": f"exports/edc/{name}",
                     "correction": bool(derived["data"]["corrects"])})  # fmt: skip
        return target

    def acknowledge(self, case_id: str) -> dict[str, Any]:
        """The mock EDC reads the exported file, checks its hash and acknowledges it; the
        case closes only then (OUT-07). A real EDC adapter replaces this step."""
        sent = self._latest(case_id, "export.sent")
        target = self.store.paths.root / sent["data"]["to"]
        content = target.read_bytes()
        if sha256_bytes(content) != sent["data"]["file"]["sha256"]:
            raise Flow4Refused(f"{target.name} changed after export")
        ack = {"file": target.name, "sha256": sent["data"]["file"]["sha256"],
               "status": "accepted", "received_at": format_time(self.store.clock()),
               "receiver": "mock EDC"}  # fmt: skip
        ack_path = self.store.paths.edc_mock / "acks" / f"{target.stem}.json"
        if not ack_path.exists():
            write_atomic(ack_path, json_bytes(ack), overwrite=False)
        self._event(case_id, "export.acknowledged", SYSTEM_EDC, {"ack": ack})
        for to in ("role:cec_coordinator", "role:safety_specialist"):
            self.flow3._notify(case_id, to, "adjudication_complete", SYSTEM_EDC)
        return ack

    def pending(self) -> list[tuple[str, str]]:
        """(case, step) for the runner: derive, export or acknowledge."""
        out = [(cs.case_id, "derive") for cs in self.store.list_case_states(State.OUTCOME)]
        for cs in self.store.list_case_states(State.EXPORT):
            types = [e["type"] for e in self.store.events(cs.case_id)]
            last = max(i for i, t in enumerate(types) if t == "outcome.derived")
            out.append((cs.case_id, "acknowledge" if "export.sent" in types[last:] else "export"))
        return out

    def run(self, case_id: str, step: str) -> None:
        {"derive": self.derive_outcome, "export": self.export,
         "acknowledge": self.acknowledge}[step](case_id)  # fmt: skip

    # -- re-adjudication ------------------------------------------------------------------------

    def start_readjudication(self, case_id: str, user_id: str, password: str,
                             reason: str) -> None:  # fmt: skip
        """RDJ-01 to RDJ-03: a new round; nothing earlier is edited or removed."""
        user = load_users(self.config_dir).get(user_id)
        if user is None or user["role"] != "cec_coordinator":
            raise SignatureRefused("only the CEC Coordinator can start a re-adjudication")
        if not reason.strip():
            raise Flow4Refused("give a reason for the re-adjudication")
        if self.store.state(case_id).state != State.CLOSED:
            raise Flow4Refused("only a closed case can be re-adjudicated")
        derived = self._latest(case_id, "outcome.derived")
        signature = sign(user, password, READJ_MEANING,
                         [{"name": "outcome", **derived["data"]["outcome"]}])  # fmt: skip

        def still_closed(prior: list[dict[str, Any]]) -> None:  # started in another tab meanwhile
            if replay(prior).state != State.CLOSED:
                raise Flow4Refused("only a closed case can be re-adjudicated")

        self._event(case_id, "readjudication.started", user_id,
                    {"after_round": derived["data"]["round"],
                     "outcome": derived["data"]["outcome"]}, still_closed,
                    reason=reason.strip(), signature=signature)  # fmt: skip
        self.flow3._notify(case_id, "role:safety_specialist", "readjudication_started", user_id)
