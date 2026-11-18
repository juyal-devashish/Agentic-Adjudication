"""Adjudication forms (spec §11.4, ADJ-01, ADJ-06; plan P-43).

A form is a versioned YAML file in `data/config/<STUDY>/forms/`. Each field has a
comparator that decides whether two answers agree: `exact`, `{date_tolerance_days: n}`
or `none` (free text, never compared). `show_if` hides a field unless another field has
a given value; a hidden field has no answer.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, field_validator


class FormRefused(Exception):
    pass


class FormField(BaseModel):
    id: str
    label: str
    type: Literal["enum", "date", "text"]
    # (value, label) pairs, in the form's order: a list, because the round's stored copy
    # is canonical JSON with sorted keys, which would reorder a mapping.
    options: list[tuple[str, str]] = []
    comparator: str | dict[str, int] = "exact"
    required: bool = True
    show_if: dict[str, str] | None = None

    @field_validator("options", mode="before")
    @classmethod
    def _pairs(cls, v: Any) -> Any:
        return list(v.items()) if isinstance(v, dict) else v

    @property
    def values(self) -> list[str]:
        return [v for v, _ in self.options]

    def label_of(self, value: str) -> str:
        return dict(self.options).get(value, value)

    @property
    def compared(self) -> bool:
        return self.comparator != "none"

    def shown(self, answers: dict[str, Any]) -> bool:
        return all(answers.get(k) == v for k, v in (self.show_if or {}).items())

    def words(self, value: Any) -> str:
        """An answer as a person reads it: 'type_1' -> 'Type 1'; a hidden field -> '-'."""
        if value is None or value == "":
            return "-"
        return dict(self.options).get(value, str(value))

    def agree(self, a: Any, b: Any) -> bool:
        """Whether two answers agree under this field's comparator (ADJ-06). Two hidden
        answers agree; a hidden answer never agrees with a given one."""
        if not self.compared:
            return True
        if a is None or b is None:
            return a is None and b is None
        if isinstance(self.comparator, dict) and "date_tolerance_days" in self.comparator:
            days = abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
            return days <= self.comparator["date_tolerance_days"]
        return a == b


class Form(BaseModel):
    form_id: str
    version: int
    title: str
    fields: list[FormField]

    @classmethod
    def load(cls, config_dir: Path, study: str, name: str) -> Form:
        return cls(**yaml.safe_load((config_dir / study / "forms" / f"{name}.yaml").read_text()))

    def field(self, field_id: str) -> FormField:
        return next(f for f in self.fields if f.id == field_id)

    @property
    def compared_ids(self) -> list[str]:
        return [f.id for f in self.fields if f.compared]

    def with_dependents(self, ids: list[str]) -> list[str]:
        """`ids` plus every field whose `show_if` depends on one of them, in form order. A
        field shown only by an open decision is open with it: two hidden answers to it are
        not an agreement while that decision may still show it."""
        open_ids = set(ids)
        for f in self.fields:  # show_if refers to an earlier field: one pass is enough
            if set(f.show_if or {}) & open_ids:
                open_ids.add(f.id)
        return [f.id for f in self.fields if f.id in open_ids]

    def validate(
        self,
        answers: dict[str, Any],
        only: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Clean and check one person's answers; return them, every field of `only` (or of
        the form) present, hidden fields as None. `context` holds values already settled
        (e.g. the agreed fields when Adjudicator 3 answers only the disputed ones), used
        for `show_if`. Raises FormRefused with a sentence a person can act on."""
        ids = only if only is not None else [f.id for f in self.fields]
        seen = {**(context or {}), **{k: answers.get(k) for k in ids}}
        out: dict[str, Any] = {}
        for f in (self.field(i) for i in ids):
            value = answers.get(f.id)
            if isinstance(value, str):
                value = value.strip() or None
            if not f.shown(seen):
                out[f.id] = None
                continue
            if value is None:
                if f.required:
                    raise FormRefused(f"answer '{f.label}'")
                out[f.id] = None
                continue
            if f.type == "enum" and value not in f.values:
                raise FormRefused(f"'{value}' is not an option for '{f.label}'")
            if f.type == "date":
                try:
                    value = date.fromisoformat(str(value)).isoformat()
                except ValueError as exc:
                    raise FormRefused(f"'{f.label}' needs a date (YYYY-MM-DD)") from exc
            out[f.id] = value
        return out
