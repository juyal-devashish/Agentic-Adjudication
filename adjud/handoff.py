"""Handoff to Flow 2.

After submission the runner writes handoff/flow2_manifest.json and appends
handoff.flow2_ready. The manifest is the contract with Flow 2: everything Flow 2 needs,
with hashes, and nothing from documents/original/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adjud.deid.policy import Policy
from adjud.documents import documents_of
from adjud.events import EventDraft
from adjud.notify import Outbox
from adjud.store import FileCaseStore, json_bytes

MANIFEST = "handoff/flow2_manifest.json"
SYSTEM = "system:handoff"


def _ref(store: FileCaseStore, case_id: str, rel: str) -> dict[str, str]:
    from adjud.events import sha256_bytes

    return {"path": rel, "sha256": sha256_bytes(store.get_file(case_id, rel))}


def build_manifest(
    store: FileCaseStore, case_id: str, config_dir: Path, study: str = "DEMO01"
) -> dict[str, Any]:
    events = store.events(case_id)
    created = events[0]["data"]
    signed = next(e for e in reversed(events) if e["type"] == "submission.signed")
    snapshot = created["snapshot"]["path"]
    eligibility = created["eligibility"]["path"]
    for e in events:  # latest snapshot after source changes
        if e["type"] == "source.changed":
            snapshot, eligibility = e["data"]["snapshot"]["path"], e["data"]["eligibility"]["path"]
    elig = store.get_json(case_id, eligibility)
    docs = documents_of(store, case_id)
    documents = []
    for rec in signed["data"]["files"]:
        done = docs.processed[rec["name"]]
        report = store.get_json(case_id, done["report"]["path"])
        documents.append(
            {
                "path": rec["path"],
                "sha256": rec["sha256"],
                "source_name": rec["name"],
                "document_type": docs.files[rec["name"]].label
                if rec["name"] in docs.files
                else None,
                "pages": report.get("pages"),
                "redaction_report": done["report"]["path"],
                "verification": "passed",
                "ai_used": False,
            }
        )
    policy = Policy.for_study(config_dir, study)
    sig = signed["signature"]
    return {
        "case_id": case_id,
        "study": created["study"],
        "subject": created["subject"],
        "created_at": signed["at"],
        "ae_snapshot": _ref(store, case_id, snapshot),
        "eligibility": {
            **_ref(store, case_id, eligibility),
            "rule_set": elig["rule_set"],
            "version": elig["version"],
        },
        "related_data": _ref(store, case_id, "source/related_data.json"),
        "prior_events": _ref(store, case_id, "source/prior_events.json"),
        "documents": documents,
        "deid_policy_version": policy.version,
        "submission": {
            "signer": sig["signer"],
            "meaning": sig["meaning"],
            "signed_at": signed["at"],
            "record_hash": sig["record_hash"],
        },
    }


def write_handoff(
    store: FileCaseStore, case_id: str, config_dir: Path, outbox: Outbox, study: str = "DEMO01"
) -> dict[str, Any]:
    """Write the manifest (once; a re-run after a crash reuses an identical file) and
    record handoff.flow2_ready."""
    manifest = build_manifest(store, case_id, config_dir, study)
    content = json_bytes(manifest)
    path = store.case_dir(case_id) / MANIFEST
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f"{case_id}: an existing {MANIFEST} differs; not overwriting")
        sha = _ref(store, case_id, MANIFEST)["sha256"]
    else:
        sha = store.put_file(case_id, MANIFEST, content)
    store.append_event(
        case_id,
        EventDraft(
            type="handoff.flow2_ready",
            actor=SYSTEM,
            data={
                "manifest": {"path": MANIFEST, "sha256": sha},
                "documents": len(manifest["documents"]),
            },
        ),
    )
    site = f"site:{manifest['subject'].split('-')[1]}"
    for to, template in (
        (site, "submission_received"),
        ("role:safety_specialist", "documents_received"),
    ):
        note = outbox.queue(case_id, to, template)
        store.append_event(
            case_id,
            EventDraft(
                type="notification.queued",
                actor=SYSTEM,
                data={"to": to, "template": template, "id": note["id"]},
            ),
        )
    return manifest


def pending_handoffs(store: FileCaseStore) -> list[str]:
    from adjud.workflow import State

    return [
        cs.case_id
        for cs in store.list_case_states(State.SUBMITTED)
        if not any(e["type"] == "handoff.flow2_ready" for e in store.events(cs.case_id))
    ]


def read_manifest(store: FileCaseStore, case_id: str) -> dict[str, Any] | None:
    path = store.case_dir(case_id) / MANIFEST
    return json.loads(path.read_text()) if path.exists() else None
