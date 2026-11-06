"""Declarative eligibility rules (no eval()). See config/DEMO01/eligibility_rules.yaml."""
from __future__ import annotations

import datetime as dt
from typing import Any


class RuleError(ValueError):
    """Raised for a broken rule definition (a configuration error, not a data error)."""


LEAF_OPS = {"equals", "not_equals", "in_list", "not_in_list", "exists",
            "on_or_after_field", "before_field", "within_days_of_field"}


def _date(v: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _leaf(node: dict, ctx: dict, lists: dict) -> dict:
    ops = [k for k in node if k in LEAF_OPS]
    if len(ops) != 1 or "field" not in node:
        raise RuleError(f"rule {node.get('id')}: needs 'field' and exactly one operator of {sorted(LEAF_OPS)}")
    op, field = ops[0], node["field"]
    expected = node[op]
    actual = ctx.get(field, "")
    res = {"id": node.get("id"), "description": node.get("description", ""), "field": field, "operator": op,
           "expected": expected, "actual": actual, "passed": False, "reason": ""}
    if op == "exists":
        res["passed"] = (actual not in ("", None)) == bool(expected)
        return res
    if actual in ("", None):
        res["reason"] = "missing value"
        return res
    if op == "equals":
        res["passed"] = actual == expected
    elif op == "not_equals":
        res["passed"] = actual != expected
    elif op in ("in_list", "not_in_list"):
        if expected not in lists:
            raise RuleError(f"rule {node.get('id')}: unknown list '{expected}'")
        inside = actual in lists[expected]
        res["passed"] = inside if op == "in_list" else not inside
    else:
        other = ctx.get(expected, "")
        res["compared_with"] = other
        a, b = _date(actual), _date(other)
        if a is None or b is None:
            res["reason"] = "missing or invalid date"
            return res
        if op == "on_or_after_field":
            res["passed"] = a >= b
        elif op == "before_field":
            res["passed"] = a < b
        else:
            res["passed"] = abs((a - b).days) <= int(node.get("days", 0))
    if not res["passed"] and not res["reason"]:
        res["reason"] = "condition not met"
    return res


def _node(node: dict, ctx: dict, lists: dict) -> dict:
    for group in ("all", "any"):
        if group in node:
            children = [_node(c, ctx, lists) for c in node[group]]
            passed = all(c["passed"] for c in children) if group == "all" else any(c["passed"] for c in children)
            return {"id": node.get("id"), "description": node.get("description", ""), "group": group,
                    "passed": passed, "children": children}
    return _leaf(node, ctx, lists)


def evaluate(rule_set: dict, ctx: dict[str, Any]) -> dict:
    """Evaluate a rule set. Top-level 'all' or 'any'. Returns a fully explained result."""
    lists = rule_set.get("lists", {})
    top = "all" if "all" in rule_set else "any" if "any" in rule_set else None
    if top is None:
        raise RuleError("rule set needs a top-level 'all' or 'any'")
    results = [_node(n, ctx, lists) for n in rule_set[top]]
    eligible = all(r["passed"] for r in results) if top == "all" else any(r["passed"] for r in results)
    return {"rule_set": rule_set.get("rule_set"), "version": rule_set.get("version"), "eligible": eligible,
            "failed_rules": [r["id"] for r in results if not r["passed"]], "results": results}
