"""Demo electronic signatures (mvp-flow-1.md F1-26; spec.md SIG-02, SIG-03).

NOT Part 11 compliant: there is no real authentication. A signature re-checks the demo
user's password and records signer, meaning, time (the event's `at`) and the hashes of
exactly what was signed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bcrypt
import yaml

from adjud.events import canonical_json, sha256_bytes


class SignatureRefused(Exception):
    pass


def load_users(config_dir: Path) -> dict[str, dict[str, Any]]:
    users = yaml.safe_load((Path(config_dir) / "users.yaml").read_text())["users"]
    return {u["id"]: u for u in users}


def check_password(user: dict[str, Any], password: str) -> bool:
    hashed = user.get("password_hash", "")
    return bool(hashed) and bcrypt.checkpw(password.encode(), hashed.encode())


def sign(
    user: dict[str, Any], password: str, meaning: str, records: list[dict[str, str]]
) -> dict[str, Any]:
    """Return the signature block for an event. `records`: [{name, path, sha256}]."""
    if not check_password(user, password):
        raise SignatureRefused("wrong password")
    body = {"meaning": meaning, "records": sorted(records, key=lambda r: r["path"])}
    return {
        "signer_id": user["id"],
        "signer": user["name"],
        "meaning": meaning,
        "records": body["records"],
        "record_hash": sha256_bytes(canonical_json(body).encode()),
    }
