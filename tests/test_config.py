"""Smoke test: committed config parses, matches the test kit where it should, and holds
the keys later steps rely on."""

from pathlib import Path

import bcrypt
import yaml

from adjud.ai.config import AiConfig

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "config"
KIT_CONFIG = ROOT / "data" / "flow1-testkit" / "config"


def load(base: Path, rel: str) -> dict:
    return yaml.safe_load((base / rel).read_text())


def test_rules_and_related_data_are_the_kits():
    for rel in ("DEMO01/eligibility_rules.yaml", "DEMO01/related_data.yaml"):
        assert load(CONFIG, rel) == load(KIT_CONFIG, rel), rel


def test_users_are_the_kits_with_a_data_manager_and_bcrypt_hashes():
    ours = load(CONFIG, "users.yaml")["users"]
    kit = load(KIT_CONFIG, "users.yaml")["users"]
    # Plan P-25: the data manager replaces the kit's study admin; everyone else is the kit's.
    expected = [
        {"id": "dm.dana", "role": "data_manager"}
        if u["role"] == "study_admin"
        else {k: u[k] for k in ("id", "role")}
        for u in kit
    ]
    assert [{k: u[k] for k in ("id", "role")} for u in ours] == expected
    for u in ours:
        assert bcrypt.checkpw(b"demo", u["password_hash"].encode())


def test_study_config_has_ai_off_by_default():
    study = load(CONFIG, "DEMO01/study.yaml")
    assert study["study"] == "DEMO01"
    ai = AiConfig.model_validate(study["ai"])
    assert ai.mode == "off" and not any(ai.functions.values())
    assert ai.model == "claude-haiku-5-5"  # plan P-26
    assert study["sites"] == load(KIT_CONFIG, "DEMO01/study.yaml")["sites"]


def test_deid_policy_matches_kit_categories():
    ours, kit = load(CONFIG, "DEMO01/deid_policy.yaml"), load(KIT_CONFIG, "DEMO01/deid_policy.yaml")
    assert ours["date_policy"] == "dob_only"
    for key in ("must_redact", "should_redact", "allow_list", "context_labels"):
        assert ours[key] == kit[key], key
