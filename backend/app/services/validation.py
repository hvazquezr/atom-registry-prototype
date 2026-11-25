"""The validation pipeline for atom values.

Order: reject unknown and read-only keys → required → type coercion → ranges and lengths →
regex (RE2, linear time, so a pattern cannot hang the server) → options → calculated fields →
validate calculated results. All failures are collected and reported together.
"""

from __future__ import annotations

import datetime as dt
import math

import re2

from app.errors import ValidationFailed, field_error
from app.services import expressions

FIELD_TYPES = ("text", "number", "date", "list", "options")
LIST_ITEM_TYPES = ("text", "number", "date")


def is_empty(value) -> bool:
    return value is None or value == "" or value == []


def _compile_regex(pattern: str):
    try:
        return re2.compile(pattern)
    except re2.error as exc:
        raise ValueError(f"Invalid regular expression: {exc}") from exc


def validate_value(f: dict, value, label: str | None = None) -> tuple[object, list[str]]:
    """Coerce and check one non-empty value. Returns (clean_value, messages)."""
    t = f["type"]
    config = f.get("config") or {}
    msgs: list[str] = []

    if t == "text":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            return value, ["must be text"]
        if (mn := config.get("min_length")) is not None and len(value) < mn:
            msgs.append(f"must be at least {mn} characters")
        if (mx := config.get("max_length")) is not None and len(value) > mx:
            msgs.append(f"must be at most {mx} characters")
        if pattern := config.get("regex"):
            if not _compile_regex(pattern).fullmatch(value):
                msgs.append(config.get("regex_message") or f"must match the pattern {pattern}")
        return value, msgs

    if t == "number":
        if isinstance(value, bool):
            return value, ["must be a number"]
        if isinstance(value, str):
            try:
                value = float(value.strip()) if any(c in value for c in ".eE") else int(value.strip())
            except ValueError:
                return value, ["must be a number"]
        if not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            return value, ["must be a number"]
        if config.get("integer_only"):
            if isinstance(value, float) and not value.is_integer():
                msgs.append("must be a whole number")
            else:
                value = int(value)
        if (mn := config.get("min")) is not None and value < mn:
            msgs.append(f"must be at least {mn}")
        if (mx := config.get("max")) is not None and value > mx:
            msgs.append(f"must be at most {mx}")
        return value, msgs

    if t == "date":
        if isinstance(value, dt.datetime):
            value = value.date().isoformat()
        elif isinstance(value, dt.date):
            value = value.isoformat()
        if not isinstance(value, str):
            return value, ["must be a date (YYYY-MM-DD)"]
        try:
            parsed = dt.date.fromisoformat(value.strip()[:10])
        except ValueError:
            return value, ["must be a date (YYYY-MM-DD)"]
        value = parsed.isoformat()
        if (mn := config.get("min")) and parsed < dt.date.fromisoformat(mn):
            msgs.append(f"must be on or after {mn}")
        if (mx := config.get("max")) and parsed > dt.date.fromisoformat(mx):
            msgs.append(f"must be on or before {mx}")
        return value, msgs

    if t == "options":
        allowed = [c["value"] for c in config.get("choices", [])]
        if config.get("multiple"):
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                return value, ["must be a list of choices"]
            bad = [v for v in value if v not in allowed]
            if bad:
                msgs.append(f"has values that are not allowed: {', '.join(bad)}")
            if len(set(value)) != len(value):
                msgs.append("has a choice more than once")
            if (mn := config.get("min_selected")) is not None and len(value) < mn:
                msgs.append(f"needs at least {mn} choices")
            if (mx := config.get("max_selected")) is not None and len(value) > mx:
                msgs.append(f"allows at most {mx} choices")
            # keep the order the choices are defined in
            value = [v for v in allowed if v in value] + [v for v in value if v not in allowed]
            return value, msgs
        if not isinstance(value, str):
            return value, ["must be one of the choices"]
        if value not in allowed:
            msgs.append(f"must be one of: {', '.join(allowed)}")
        return value, msgs

    if t == "list":
        if not isinstance(value, list):
            return value, ["must be a list"]
        item_field = {"type": config.get("item_type", "text"), "config": config.get("item") or {}}
        clean = []
        for i, item in enumerate(value):
            if is_empty(item):
                msgs.append(f"item {i + 1} is empty")
                continue
            c, m = validate_value(item_field, item)
            clean.append(c)
            msgs.extend(f"item {i + 1} {x}" for x in m)
        if (mn := config.get("min_items")) is not None and len(clean) < mn:
            msgs.append(f"needs at least {mn} items")
        if (mx := config.get("max_items")) is not None and len(clean) > mx:
            msgs.append(f"allows at most {mx} items")
        if config.get("unique_items") and len(set(map(str, clean))) != len(clean):
            msgs.append("has duplicate items")
        return clean, msgs

    return value, [f"has an unknown type {t}"]


def validate_atom_values(
    fields: list[dict],
    proposed: dict,
    existing: dict | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Validate a full set (create) or a partial patch (update, when `existing` is given).

    Returns the complete stored values, including calculated fields.
    Raises ValidationFailed listing every problem.
    """
    by_key = {f["key"]: f for f in fields}
    errors: list[dict] = []

    for key in proposed:
        if key not in by_key:
            errors.append(field_error(key, "unknown_field", f"There is no field called {key!r}."))
        elif by_key[key].get("calculated"):
            errors.append(field_error(key, "read_only", f"{by_key[key]['label']} is calculated and can't be set."))

    # Start from the existing values (keeps values of deprecated fields) and apply the patch.
    merged = dict(existing or {})
    for key, value in proposed.items():
        if key in by_key and not by_key[key].get("calculated"):
            merged[key] = value

    clean: dict = {k: v for k, v in merged.items() if k not in by_key}  # legacy keys kept as-is
    for f in fields:
        if f.get("calculated"):
            continue
        key = f["key"]
        value = merged.get(key)
        if is_empty(value):
            if f.get("required"):
                errors.append(field_error(key, "required", f"{f['label']} is required."))
            continue
        try:
            value, msgs = validate_value(f, value)
        except ValueError as exc:  # a broken regex in the definition
            errors.append(field_error(key, "invalid_definition", str(exc)))
            continue
        if msgs:
            errors.append(field_error(key, "invalid", f"{f['label']} " + "; ".join(msgs) + "."))
        clean[key] = value

    if errors:
        raise ValidationFailed(errors)

    clean, calc_errors = expressions.compute(fields, clean, now=now)
    errors.extend(calc_errors)
    for f in fields:
        if not f.get("calculated"):
            continue
        key = f["key"]
        value = clean.get(key)
        if is_empty(value):
            clean.pop(key, None)
            if f.get("required") and not any(e["field"] == key for e in errors):
                errors.append(
                    field_error(key, "required", f"{f['label']} is required but could not be calculated (an input is empty).")
                )
            continue
        value, msgs = validate_value(f, value)
        if msgs:
            errors.append(field_error(key, "invalid", f"{f['label']} (calculated) " + "; ".join(msgs) + "."))
        clean[key] = value

    if errors:
        raise ValidationFailed(errors)
    return clean


def recompute_time_dependent(fields: list[dict], values: dict, now: dt.datetime | None = None) -> dict:
    """Recalculate only the fields that depend on today()/now(). Used when reading atoms."""
    keys = expressions.time_dependent_keys(fields)
    if not keys:
        return values
    out, _ = expressions.compute(fields, values, now=now, only=keys)
    for k in keys:
        if out.get(k) is None:
            out.pop(k, None)
    return out
