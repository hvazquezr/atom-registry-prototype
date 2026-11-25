"""Field definitions (the draft meta atom) and schema publishing."""

from __future__ import annotations

import datetime as dt
import uuid

import re2
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError, ValidationFailed, bad_request, conflict, field_error, not_found
from app.models import FieldDefinition, SchemaVersion, User
from app.services import expressions
from app.services.validation import FIELD_TYPES, LIST_ITEM_TYPES, validate_value

KEY_PATTERN = re2.compile(r"[a-z][a-z0-9_]{0,63}")
# Words CEL reserves; a field with one of these keys could not be referenced in expressions.
RESERVED_KEYS = {
    "true", "false", "null", "in", "as", "break", "const", "continue", "else", "for", "function",
    "if", "import", "let", "loop", "package", "namespace", "return", "var", "void", "while",
    "id", "status", "revision", "schema_version", "created_at", "updated_at", "created_by", "updated_by",
}

CONFIG_KEYS = {
    "text": {"min_length", "max_length", "regex", "regex_message", "placeholder"},
    "number": {"min", "max", "integer_only", "unit"},
    "date": {"min", "max"},
    "options": {"choices", "multiple", "min_selected", "max_selected"},
    "list": {"item_type", "item", "min_items", "max_items", "unique_items"},
}


def field_to_dict(f: FieldDefinition) -> dict:
    return {
        "id": str(f.id),
        "key": f.key,
        "label": f.label,
        "description": f.description,
        "type": f.type,
        "required": f.required,
        "config": f.config or {},
        "calculated": f.calculated,
        "expression": f.expression,
        "depends_on": list(f.depends_on or []),
        "time_dependent": f.time_dependent,
        "position": f.position,
        "status": f.status,
        "system": f.system,
        "published": f.published,
    }


# --- Definition checks ------------------------------------------------------------------


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_config(ftype: str, config: dict, where: str = "config") -> list[str]:
    msgs: list[str] = []
    if ftype not in FIELD_TYPES:
        return [f"type must be one of {', '.join(FIELD_TYPES)}"]
    extra = set(config) - CONFIG_KEYS[ftype]
    if extra:
        msgs.append(f"{where} has settings that don't apply to {ftype} fields: {', '.join(sorted(extra))}")

    def check_range(lo_key, hi_key, kind):
        lo, hi = config.get(lo_key), config.get(hi_key)
        for k, v in ((lo_key, lo), (hi_key, hi)):
            if v is None:
                continue
            if kind == "int" and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                msgs.append(f"{k} must be a whole number of 0 or more")
            elif kind == "number" and not _is_number(v):
                msgs.append(f"{k} must be a number")
            elif kind == "date":
                try:
                    dt.date.fromisoformat(v)
                except (TypeError, ValueError):
                    msgs.append(f"{k} must be a date (YYYY-MM-DD)")
        try:
            if lo is not None and hi is not None and lo > hi:
                msgs.append(f"{lo_key} can't be greater than {hi_key}")
        except TypeError:
            pass

    if ftype == "text":
        check_range("min_length", "max_length", "int")
        if config.get("regex"):
            try:
                re2.compile(config["regex"])
            except re2.error as exc:
                msgs.append(f"regex is not valid: {exc}")
    elif ftype == "number":
        check_range("min", "max", "number")
    elif ftype == "date":
        check_range("min", "max", "date")
    elif ftype == "options":
        choices = config.get("choices")
        if not isinstance(choices, list) or not choices:
            msgs.append("options fields need at least one choice")
        else:
            values = []
            for c in choices:
                if not isinstance(c, dict) or not isinstance(c.get("value"), str) or not c["value"]:
                    msgs.append("each choice needs a text value")
                    break
                values.append(c["value"])
            if len(set(values)) != len(values):
                msgs.append("choice values must be unique")
        check_range("min_selected", "max_selected", "int")
        if not config.get("multiple") and (config.get("min_selected") or config.get("max_selected")):
            msgs.append("min_selected and max_selected only apply to multiple choice")
    elif ftype == "list":
        item_type = config.get("item_type", "text")
        if item_type not in LIST_ITEM_TYPES:
            msgs.append(f"item_type must be one of {', '.join(LIST_ITEM_TYPES)}")
        else:
            msgs.extend(check_config(item_type, config.get("item") or {}, "item"))
        check_range("min_items", "max_items", "int")
    return msgs


def check_expression(field: dict, fields: list[dict]) -> dict:
    """Check one calculated field against the other fields.

    Returns {"depends_on", "time_dependent", "sample_result"} or raises ExpressionError.
    """
    parsed = expressions.parse(field.get("expression") or "")
    by_key = {f["key"]: f for f in fields if f["key"] != field["key"]}
    if field["key"] in parsed.variables:
        raise expressions.ExpressionError("self_reference", "A field can't use its own value.")
    unknown = sorted(v for v in parsed.variables if v not in by_key)
    if unknown:
        raise expressions.ExpressionError("unknown_field", f"Unknown field: {', '.join(unknown)}.")
    deprecated = sorted(v for v in parsed.variables if by_key[v].get("status") == "deprecated")
    if deprecated:
        raise expressions.ExpressionError("deprecated_field", f"Uses deprecated field: {', '.join(deprecated)}.")
    depends_on = sorted(parsed.variables)

    candidate = dict(field, depends_on=depends_on)
    all_fields = [f for f in fields if f["key"] != field["key"]] + [candidate]
    expressions.calculation_order(all_fields)  # raises on cycles

    variables = {k: expressions.to_cel(expressions.sample_value(by_key[k]), by_key[k]["type"], by_key[k].get("config")) for k in depends_on}
    result = expressions.from_cel(expressions.evaluate(parsed, variables), field["type"], field.get("config"))
    time_dependent = candidate["key"] in expressions.time_dependent_keys(all_fields)
    return {"depends_on": depends_on, "time_dependent": time_dependent, "sample_result": result}


def evaluate_with_values(field: dict, fields: list[dict], values: dict):
    """Evaluate a calculated field's expression against values the admin typed in."""
    parsed = expressions.parse(field.get("expression") or "")
    by_key = {f["key"]: f for f in fields}
    variables = {}
    for k in parsed.variables:
        if k not in by_key:
            raise expressions.ExpressionError("unknown_field", f"Unknown field: {k}.")
        if values.get(k) is None:
            raise expressions.ExpressionError("missing_value", f"Enter a value for {k} to test.")
        clean, msgs = validate_value(by_key[k], values[k])
        if msgs:
            raise expressions.ExpressionError("invalid_value", f"{k} " + "; ".join(msgs))
        variables[k] = expressions.to_cel(clean, by_key[k]["type"], by_key[k].get("config"))
    return expressions.from_cel(expressions.evaluate(parsed, variables), field["type"], field.get("config"))


def check_definition(field: dict, fields: list[dict]) -> tuple[list[dict], dict | None]:
    """Full check of one definition. Returns (errors, expression_info)."""
    errors: list[dict] = []
    key = field.get("key") or ""
    if not KEY_PATTERN.fullmatch(key):
        errors.append(field_error("key", "invalid", "Key must start with a letter and use only lowercase letters, digits and _."))
    elif key in RESERVED_KEYS:
        errors.append(field_error("key", "reserved", f"{key!r} is reserved."))
    if not (field.get("label") or "").strip():
        errors.append(field_error("label", "required", "Label is required."))
    for msg in check_config(field.get("type"), field.get("config") or {}):
        errors.append(field_error("config", "invalid", msg[0].upper() + msg[1:] + "."))
    info = None
    if field.get("calculated"):
        if not errors:
            try:
                info = check_expression(field, fields)
            except expressions.ExpressionError as exc:
                errors.append(field_error("expression", exc.code, exc.message))
    elif field.get("expression"):
        errors.append(field_error("expression", "not_calculated", "Only calculated fields have an expression."))
    return errors, info


def check_draft(fields: list[dict]) -> list[dict]:
    """Check every active definition together. Returns errors tagged with the field key."""
    active = [f for f in fields if f["status"] == "active"]
    errors: list[dict] = []
    for f in active:
        if f.get("calculated"):
            try:
                check_expression(f, active)
            except expressions.ExpressionError as exc:
                errors.append(field_error(f["key"], exc.code, f"{f['label']}: {exc.message}"))
    return errors


# --- CRUD -------------------------------------------------------------------------------


def list_fields(db: Session, include_deprecated: bool = True) -> list[FieldDefinition]:
    q = select(FieldDefinition).order_by(FieldDefinition.position, FieldDefinition.created_at)
    if not include_deprecated:
        q = q.where(FieldDefinition.status == "active")
    return list(db.scalars(q))


def _refresh_derived(db: Session) -> None:
    """Recompute depends_on/time_dependent for all calculated fields (time dependency propagates)."""
    rows = list_fields(db)
    dicts = [field_to_dict(r) for r in rows]
    active = [d for d in dicts if d["status"] == "active"]
    try:
        td = expressions.time_dependent_keys(active)
    except expressions.ExpressionError:
        td = set()
    for r in rows:
        r.time_dependent = r.key in td


def save_field(db: Session, data: dict, field_id: uuid.UUID | None = None) -> FieldDefinition:
    existing_rows = list_fields(db)
    current = None
    if field_id:
        current = next((r for r in existing_rows if r.id == field_id), None)
        if not current:
            raise not_found("Field")
        if current.published and data.get("key") and data["key"] != current.key:
            raise conflict("key_frozen", "The key of a published field can't change.")
        if current.system and data.get("type") and data["type"] != current.type:
            raise conflict("system_field", "The type of a built-in field can't change.")
        merged = field_to_dict(current) | data
    else:
        if any(r.key == data.get("key") for r in existing_rows):
            raise conflict("duplicate_key", f"A field with key {data.get('key')!r} already exists.")
        merged = {
            "key": data.get("key"), "label": data.get("label"), "description": data.get("description") or "",
            "type": data.get("type"), "required": bool(data.get("required")), "config": data.get("config") or {},
            "calculated": bool(data.get("calculated")), "expression": data.get("expression"),
            "status": "active", "depends_on": [],
            "position": data.get("position") if data.get("position") is not None else len(existing_rows),
        }
    if not merged.get("calculated"):
        merged["expression"] = None

    others = [field_to_dict(r) for r in existing_rows if r.id != field_id]
    errors, info = check_definition(merged, [o for o in others if o["status"] == "active"])
    if errors:
        raise ValidationFailed(errors)

    # Would this change break another calculated field?
    proposed_all = [o for o in others] + [dict(merged, depends_on=(info or {}).get("depends_on", []))]
    broken = [e for e in check_draft(proposed_all) if e["field"] != merged["key"]]
    if broken:
        raise ValidationFailed([field_error(e["field"], "would_break", e["message"]) for e in broken])

    row = current or FieldDefinition()
    row.key = merged["key"]
    row.label = merged["label"].strip()
    row.description = merged.get("description") or ""
    row.type = merged["type"]
    row.required = bool(merged.get("required"))
    row.config = merged.get("config") or {}
    row.calculated = bool(merged.get("calculated"))
    row.expression = merged.get("expression")
    row.depends_on = (info or {}).get("depends_on", [])
    row.position = merged.get("position", 0)
    if current is None:
        db.add(row)
    db.flush()
    _refresh_derived(db)
    db.flush()
    return row


def set_field_status(db: Session, field_id: uuid.UUID, status: str) -> FieldDefinition:
    rows = list_fields(db)
    row = next((r for r in rows if r.id == field_id), None)
    if not row:
        raise not_found("Field")
    if status == "deprecated":
        if row.system:
            raise conflict("system_field", "Built-in fields can't be deprecated.")
        users = [r.label for r in rows if r.status == "active" and r.calculated and row.key in (r.depends_on or [])]
        if users:
            raise conflict("in_use", f"Used by calculated fields: {', '.join(users)}. Change those first.")
    else:
        dicts = [field_to_dict(r) for r in rows if r.id != row.id]
        errors, _ = check_definition(dict(field_to_dict(row), status="active"), [d for d in dicts if d["status"] == "active"])
        if errors:
            raise ValidationFailed(errors)
    row.status = status
    db.flush()
    _refresh_derived(db)
    db.flush()
    return row


def delete_field(db: Session, field_id: uuid.UUID) -> None:
    row = db.get(FieldDefinition, field_id)
    if not row:
        raise not_found("Field")
    if row.published or row.system:
        raise conflict("published", "Published fields can't be deleted. Deprecate it instead.")
    users = [r.label for r in list_fields(db) if r.calculated and row.key in (r.depends_on or [])]
    if users:
        raise conflict("in_use", f"Used by calculated fields: {', '.join(users)}.")
    db.delete(row)
    db.flush()


def reorder(db: Session, keys: list[str]) -> list[FieldDefinition]:
    rows = {r.key: r for r in list_fields(db)}
    if set(keys) != set(rows):
        raise bad_request("bad_order", "List every field key exactly once.")
    for i, k in enumerate(keys):
        rows[k].position = i
    db.flush()
    return list_fields(db)


# --- Publishing -------------------------------------------------------------------------

SNAPSHOT_KEYS = ("key", "label", "description", "type", "required", "config", "calculated",
                 "expression", "depends_on", "time_dependent", "position")


def snapshot(rows: list[FieldDefinition]) -> list[dict]:
    return [{k: field_to_dict(r)[k] for k in SNAPSHOT_KEYS} for r in rows if r.status == "active"]


def latest_schema(db: Session) -> SchemaVersion | None:
    return db.scalars(select(SchemaVersion).order_by(SchemaVersion.version.desc()).limit(1)).first()


def get_schema(db: Session, version: int) -> SchemaVersion:
    s = db.get(SchemaVersion, version)
    if not s:
        raise not_found("Schema version")
    return s


def require_schema(db: Session) -> SchemaVersion:
    s = latest_schema(db)
    if not s:
        raise AppError(409, "no_schema", "No schema has been published yet.")
    return s


def draft_changes(db: Session) -> dict:
    """What publishing now would change compared to the latest published schema."""
    current = {f["key"]: f for f in snapshot(list_fields(db))}
    latest = latest_schema(db)
    published = {f["key"]: f for f in (latest.fields if latest else [])}
    added = sorted(set(current) - set(published))
    removed = sorted(set(published) - set(current))
    changed = sorted(k for k in set(current) & set(published) if current[k] != published[k])
    recalc = sorted(
        k for k in current
        if current[k]["calculated"]
        and (k not in published or current[k]["expression"] != published[k].get("expression")
             or current[k]["type"] != published[k]["type"])
    )
    return {
        "latest_version": latest.version if latest else None,
        "added": added, "removed": removed, "changed": changed, "recalculate": recalc,
        "has_changes": bool(added or removed or changed),
    }


def publish(db: Session, user: User | None, notes: str = "") -> tuple[SchemaVersion, list[str]]:
    rows = list_fields(db)
    dicts = [field_to_dict(r) for r in rows]
    errors = check_draft(dicts)
    if errors:
        raise ValidationFailed(errors)
    changes = draft_changes(db)
    if changes["latest_version"] is not None and not changes["has_changes"]:
        raise conflict("no_changes", "The draft is the same as the latest published schema.")
    next_version = (db.scalar(select(func.max(SchemaVersion.version))) or 0) + 1
    sv = SchemaVersion(version=next_version, fields=snapshot(rows), notes=notes, published_by=user.id if user else None)
    db.add(sv)
    for r in rows:
        if r.status == "active":
            r.published = True
    db.flush()
    return sv, changes["recalculate"]
