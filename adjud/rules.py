"""Declarative eligibility rules. Never uses eval().

A rule set has a top-level `all` or `any` list. Each node is either a group (`all` /
`any` with children, optionally with `id` and `description`) or a leaf with `field` and
exactly one operator. Every evaluation returns a fully explained result, so page 1 can
say in plain words why a record was or was not eligible.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

LEAF_OPS = frozenset(
    {
        "equals",
        "not_equals",
        "in_list",
        "not_in_list",
        "exists",
        "on_or_after_field",
        "before_field",
        "within_days_of_field",
    }
)
DATE_OPS = frozenset({"on_or_after_field", "before_field", "within_days_of_field"})
GROUPS = ("all", "any")


class RuleError(ValueError):
    """A broken rule definition: a configuration error, not a data error."""


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _leaf(node: dict[str, Any], ctx: dict[str, Any], lists: dict[str, list]) -> dict[str, Any]:
    ops = [k for k in node if k in LEAF_OPS]
    if len(ops) != 1 or "field" not in node:
        raise RuleError(
            f"rule {node.get('id')!r}: needs 'field' and exactly one operator of {sorted(LEAF_OPS)}"
        )
    op, field = ops[0], node["field"]
    expected = node[op]
    actual = ctx.get(field)
    res: dict[str, Any] = {
        "id": node.get("id"),
        "description": node.get("description", ""),
        "field": field,
        "operator": op,
        "expected": expected,
        "actual": actual if actual is not None else "",
        "passed": False,
        "reason": "",
    }
    if op in ("in_list", "not_in_list") and expected not in lists:
        raise RuleError(f"rule {node.get('id')!r}: unknown list {expected!r}")

    if op == "exists":
        res["passed"] = (not _empty(actual)) == bool(expected)
    elif _empty(actual):
        res["reason"] = "missing value"
    elif op == "equals":
        res["passed"] = str(actual) == str(expected)
    elif op == "not_equals":
        res["passed"] = str(actual) != str(expected)
    elif op in ("in_list", "not_in_list"):
        inside = str(actual) in {str(v) for v in lists[expected]}
        res["passed"] = inside if op == "in_list" else not inside
    else:  # date comparisons against another field
        other = ctx.get(expected)
        res["compared_with"] = other if other is not None else ""
        a, b = _as_date(actual), _as_date(other)
        if a is None or b is None:
            res["reason"] = "missing or invalid date"
        elif op == "on_or_after_field":
            res["passed"] = a >= b
        elif op == "before_field":
            res["passed"] = a < b
        else:
            if "days" not in node:
                raise RuleError(f"rule {node.get('id')!r}: within_days_of_field needs 'days'")
            res["passed"] = abs((a - b).days) <= int(node["days"])
    if not res["passed"] and not res["reason"]:
        res["reason"] = "condition not met"
    return res


def _node(node: Any, ctx: dict[str, Any], lists: dict[str, list]) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise RuleError(f"rule node must be a mapping, got {type(node).__name__}")
    groups = [g for g in GROUPS if g in node]
    if len(groups) > 1:
        raise RuleError(f"rule {node.get('id')!r}: has both 'all' and 'any'")
    if groups:
        group = groups[0]
        if LEAF_OPS & node.keys():
            raise RuleError(f"rule {node.get('id')!r}: a group cannot also have an operator")
        children_def = node[group]
        if not isinstance(children_def, list) or not children_def:
            raise RuleError(f"rule {node.get('id')!r}: '{group}' needs a non-empty list")
        children = [_node(c, ctx, lists) for c in children_def]
        results = [c["passed"] for c in children]
        passed = all(results) if group == "all" else any(results)
        return {
            "id": node.get("id"),
            "description": node.get("description", ""),
            "group": group,
            "passed": passed,
            "children": children,
        }
    return _leaf(node, ctx, lists)


def evaluate(rule_set: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a rule set against one record's context. Raises RuleError for a broken
    rule set; data problems (missing or invalid values) make rules fail with a reason."""
    tops = [g for g in GROUPS if g in rule_set]
    if len(tops) != 1:
        raise RuleError("rule set needs exactly one top-level 'all' or 'any'")
    top = tops[0]
    lists = rule_set.get("lists") or {}
    if not isinstance(rule_set[top], list) or not rule_set[top]:
        raise RuleError(f"top-level '{top}' needs a non-empty list")
    results = [_node(n, ctx, lists) for n in rule_set[top]]
    passed = [r["passed"] for r in results]
    eligible = all(passed) if top == "all" else any(passed)
    return {
        "rule_set": rule_set.get("rule_set"),
        "version": rule_set.get("version"),
        "eligible": eligible,
        "failed_rules": [r["id"] for r in results if not r["passed"]],
        "results": results,
    }


def explain(result: dict[str, Any]) -> list[str]:
    """Plain-language lines for the UI, one per rule (children indented)."""
    lines: list[str] = []

    def walk(r: dict[str, Any], depth: int) -> None:
        mark = "✓" if r["passed"] else "✗"
        label = f"{r.get('id') or ''} {r.get('description') or ''}".strip()
        if "group" in r:
            lines.append(f"{'  ' * depth}{mark} {label} ({r['group']} of:)")
            for c in r["children"]:
                walk(c, depth + 1)
        else:
            detail = f"{r['field']} = {r['actual']!r}"
            if not r["passed"]:
                detail += f" — {r['reason']}"
            lines.append(f"{'  ' * depth}{mark} {label}: {detail}")

    for r in result["results"]:
        walk(r, 0)
    return lines
