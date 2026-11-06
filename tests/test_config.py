"""Smoke test: committed config files parse and hold the keys later steps rely on."""

from pathlib import Path

import bcrypt
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "data" / "config"


def load(rel: str) -> dict:
    return yaml.safe_load((CONFIG / rel).read_text())


def test_study_config_has_ai_off_by_default():
    study = load("DEMO01/study.yaml")
    assert study["study"] == "DEMO01"
    assert study["ai"]["phi_detection"] is False


def test_eligibility_rules_parse():
    rules = load("DEMO01/eligibility_rules.yaml")
    assert rules["rule_set"] == "DEMO01-ELIG"
    assert rules["version"] == 1
    assert "adjudicable_pts" in rules["lists"]


def test_deid_policy_default_date_policy():
    assert load("DEMO01/deid_policy.yaml")["date_policy"] == "dob_only"


def test_demo_users_have_valid_bcrypt_hashes():
    users = load("users.yaml")["users"]
    roles = {u["role"] for u in users}
    assert roles == {"site", "safety_specialist", "study_admin"}
    for u in users:
        assert bcrypt.checkpw(b"demo", u["password_hash"].encode())
