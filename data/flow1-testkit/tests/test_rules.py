"""TC-RUL: eligibility rule interpreter."""
import pytest
import yaml

from adjud.rules import RuleError, evaluate

LISTS = {"adj": ["90000101"]}


def rs(*nodes, top="all"):
    return {"rule_set": "T", "version": 7, top: list(nodes), "lists": LISTS}


def test_rul01_equals():
    r = rs({"id": "R1", "field": "serious", "equals": "Y"})
    assert evaluate(r, {"serious": "Y"})["eligible"]
    res = evaluate(r, {"serious": "N"})
    assert not res["eligible"] and res["failed_rules"] == ["R1"]
    assert res["results"][0]["actual"] == "N" and res["results"][0]["expected"] == "Y"


def test_rul02_missing_value_fails_with_reason():
    res = evaluate(rs({"id": "R2", "field": "pt_code", "in_list": "adj"}), {"pt_code": ""})
    assert not res["eligible"] and res["results"][0]["reason"] == "missing value"


def test_rul03_unknown_list_is_config_error():
    with pytest.raises(RuleError):
        evaluate(rs({"id": "R2", "field": "pt_code", "in_list": "nope"}), {"pt_code": "1"})


def test_rul04_on_or_after_boundary():
    r = rs({"id": "R3a", "field": "onset_date", "on_or_after_field": "first_dose_date"})
    assert evaluate(r, {"onset_date": "2025-05-12", "first_dose_date": "2025-05-12"})["eligible"]
    assert not evaluate(r, {"onset_date": "2025-05-11", "first_dose_date": "2025-05-12"})["eligible"]
    bad = evaluate(r, {"onset_date": "2025-05-12", "first_dose_date": ""})
    assert not bad["eligible"] and "date" in bad["results"][0]["reason"]


def test_rul05_any_group_explains_children():
    r = rs({"id": "R3", "any": [
        {"id": "R3a", "field": "onset_date", "on_or_after_field": "first_dose_date"},
        {"id": "R3b", "field": "outcome", "equals": "FATAL"}]})
    ctx = {"onset_date": "2025-05-20", "first_dose_date": "2025-06-02"}
    assert evaluate(r, {**ctx, "outcome": "FATAL"})["eligible"]
    res = evaluate(r, {**ctx, "outcome": "RECOVERED"})
    assert res["failed_rules"] == ["R3"]
    assert [c["passed"] for c in res["results"][0]["children"]] == [False, False]


def test_rul06_malformed_rule_rejected():
    with pytest.raises(RuleError):
        evaluate(rs({"id": "X", "field": "a", "equals": 1, "in_list": "adj"}), {"a": 1})
    with pytest.raises(RuleError):
        evaluate({"rule_set": "T"}, {})


def test_rul07_result_records_rule_set_and_version():
    res = evaluate(rs({"id": "R1", "field": "serious", "equals": "Y"}), {"serious": "Y"})
    assert (res["rule_set"], res["version"]) == ("T", 7)


def test_rul08_within_days():
    r = rs({"id": "W", "field": "a", "within_days_of_field": "b", "days": 30})
    assert evaluate(r, {"a": "2026-01-31", "b": "2026-01-01"})["eligible"]
    assert not evaluate(r, {"a": "2026-02-01", "b": "2026-01-01"})["eligible"]


def test_rul09_study_rules_file_is_well_formed(kit):
    rules = yaml.safe_load((kit / "config/DEMO01/eligibility_rules.yaml").read_text())
    def walk(n):
        yield n
        for g in ("all", "any"):
            for c in n.get(g, []):
                yield from walk(c)
    nodes = [x for top in rules["all"] for x in walk(top)]
    assert all(n.get("id") and n.get("description") for n in nodes)
    evaluate(rules, {"serious": "Y", "pt_code": "90000101", "onset_date": "2026-01-01",
                     "first_dose_date": "2025-01-01", "outcome": "RECOVERED"})
