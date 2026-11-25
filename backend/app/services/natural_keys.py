"""Natural keys: the field(s) of an atom, or the question(s) of a form, that identify it.

Atoms: field definitions marked `natural_key` make up the key, in field order. Forms: questions
marked `natural_key` make up the key of the form's records, in form order. A key with more than
one part is a combined key.

The key is stored on each row as one normalized string (a JSON list of the parts), with a unique
index, so the database enforces it too:

  * text and choices are trimmed and compared without regard to case ("ABC " finds "abc"),
  * numbers are compared by value (2, 2.0 and "2" are the same),
  * dates as YYYY-MM-DD.

Atoms are unique across all atoms, deprecated ones included. Records are unique per form,
archived ones included. A record is keyed by the questions of the form's latest published version.
"""

from __future__ import annotations

import datetime as dt
import json
import math

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import AppError, ValidationFailed, bad_request, conflict, field_error
from app.models import Atom, Record
from app.services.validation import is_empty

# Atom field types and form answer types that can be part of a key, and how each is compared.
FIELD_KINDS = {"text": "text", "number": "number", "date": "date", "options": "text"}
ANSWER_KINDS = {"text": "text", "integer": "number", "number": "number", "date": "date", "choice": "text"}


def _number(value) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        s = value.strip()
        try:
            value = int(s)
        except ValueError:
            try:
                value = float(s)
            except ValueError:
                return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            value = int(value)
    if not isinstance(value, (int, float)):
        return None
    return str(value)


def normalize(kind: str, value) -> str | None:
    """One key part as a comparable string, or None if the value can't be read as that kind."""
    if is_empty(value):
        return None
    if kind == "number":
        return _number(value)
    if kind == "date":
        try:
            return dt.date.fromisoformat(str(value).strip()[:10]).isoformat()
        except ValueError:
            return None
    if isinstance(value, (list, dict, bool)):
        return None
    s = str(value).strip().casefold()
    return s or None


def joined(parts: list[str]) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _kind_of_field(field: dict) -> str | None:
    if field.get("calculated"):
        return None
    if field.get("type") == "options" and (field.get("config") or {}).get("multiple"):
        return None
    return FIELD_KINDS.get(field.get("type"))


def _kind_of_item(item: dict) -> str | None:
    if item.get("calculation"):
        return None
    return ANSWER_KINDS.get((item.get("input") or {}).get("type"))


# --- Atoms ------------------------------------------------------------------------------


def atom_key_fields(fields: list[dict]) -> list[dict]:
    return [f for f in fields if f.get("natural_key")]


def field_problem(field: dict) -> str | None:
    """Why a field definition can't be part of the natural key, or None if it can."""
    if field.get("calculated"):
        return "Calculated fields can't be part of the natural key."
    if field.get("type") == "options" and (field.get("config") or {}).get("multiple"):
        return "Multiple-choice fields can't be part of the natural key."
    if field.get("type") not in FIELD_KINDS:
        return f"{field.get('type')} fields can't be part of the natural key. Use text, number, date or single choice."
    return None


def atom_key(fields: list[dict], values: dict) -> str | None:
    """The stored key for these values, or None when the schema has no key or a part is empty."""
    key_fields = atom_key_fields(fields)
    if not key_fields:
        return None
    parts = []
    for f in key_fields:
        kind = _kind_of_field(f)
        part = normalize(kind, (values or {}).get(f["key"])) if kind else None
        if part is None:
            return None
        parts.append(part)
    return joined(parts)


def atom_key_values(fields: list[dict], values: dict) -> dict | None:
    key_fields = atom_key_fields(fields)
    if not key_fields:
        return None
    return {f["key"]: (values or {}).get(f["key"]) for f in key_fields}


def _atom_name(atom: Atom) -> str:
    return str((atom.values or {}).get("name") or atom.id)


def check_atom_key(db: Session, fields: list[dict], values: dict, atom_id=None) -> str | None:
    """Raise ValidationFailed if the key is missing or another atom already has it."""
    key_fields = atom_key_fields(fields)
    if not key_fields:
        return None
    key = atom_key(fields, values)
    if key is None:
        missing = [f for f in key_fields if normalize(_kind_of_field(f) or "text", (values or {}).get(f["key"])) is None]
        raise ValidationFailed([field_error(f["key"], "required", f"{f['label']} is part of the natural key, so it is required.")
                                for f in missing])
    q = select(Atom).where(Atom.natural_key == key)
    if atom_id is not None:
        q = q.where(Atom.id != atom_id)
    other = db.scalars(q.limit(1)).first()
    if other:
        label = " + ".join(f["label"] for f in key_fields)
        raise ValidationFailed([field_error(f["key"], "duplicate_key",
                                            f"Another atom ({_atom_name(other)}) already has this {label}.")
                                for f in key_fields])
    return key


def reassign_atom_keys(db: Session, fields: list[dict]) -> None:
    """When a schema is published: work out every atom's key under the new fields.

    Refused (nothing changes) when atoms would be missing a key or share one.
    """
    atoms = list(db.scalars(select(Atom).order_by(Atom.created_at)))
    keys = {a.id: atom_key(fields, a.values) for a in atoms}
    if atom_key_fields(fields):
        missing = [a for a in atoms if keys[a.id] is None]
        seen: dict[str, Atom] = {}
        clashes = []
        for a in atoms:
            k = keys[a.id]
            if k is None:
                continue
            if k in seen:
                clashes.append({"atom_id": str(a.id), "name": _atom_name(a), "same_as": str(seen[k].id),
                                "same_as_name": _atom_name(seen[k])})
            else:
                seen[k] = a
        if missing or clashes:
            parts = []
            if missing:
                parts.append(f"{len(missing)} atom(s) have no value for the key: " + ", ".join(_atom_name(a) for a in missing[:10]))
            if clashes:
                parts.append(f"{len(clashes)} atom(s) share a key with another: "
                             + ", ".join(f"{c['name']} and {c['same_as_name']}" for c in clashes[:10]))
            raise conflict("natural_key_conflict", "Can't use this natural key yet. " + "; ".join(parts) + ".",
                           {"missing": [{"atom_id": str(a.id), "name": _atom_name(a)} for a in missing], "duplicates": clashes})
    db.execute(update(Atom).values(natural_key=None))
    db.flush()
    for a in atoms:
        a.natural_key = keys[a.id]
    db.flush()


# --- Lookups ----------------------------------------------------------------------------


def lookup_key(parts: list[tuple[str, str, str | None]], path_value: str | None, query: dict[str, str],
               what: str) -> str:
    """Build the stored key from a URL.

    parts: (key, label, kind) for each part of the key, in order. Either `path_value` (only for a
    key with one part) or one query parameter per part, named by its key.
    """
    if not parts:
        raise conflict("no_natural_key", f"{what} has no natural key.")
    keys = [p[0] for p in parts]
    if path_value is not None:
        if len(parts) > 1:
            raise bad_request("combined_key", f"The natural key has {len(parts)} parts. Send each one as a query "
                              f"parameter: ?{'&'.join(k + '=…' for k in keys)}", {"key": keys})
        raw = {keys[0]: path_value}
    else:
        raw = {k: v for k, v in query.items() if k in keys}
        absent = [k for k in keys if not (raw.get(k) or "").strip()]
        if absent:
            raise bad_request("missing_key_part", f"Send every part of the natural key: {', '.join(absent)} missing.", {"key": keys})
    out = []
    for key, label, kind in parts:
        part = normalize(kind or "text", raw[key])
        if part is None:
            raise bad_request("bad_key_part", f"{label} isn't a valid {kind}.", {"key": keys})
        out.append(part)
    return joined(out)


def atom_lookup_parts(fields: list[dict]) -> list[tuple[str, str, str | None]]:
    return [(f["key"], f["label"], _kind_of_field(f)) for f in atom_key_fields(fields)]


# --- Records ----------------------------------------------------------------------------


def _items(definition: dict) -> list[dict]:
    return [i for s in (definition or {}).get("sections", []) for i in s["items"]]


def record_key_items(definition: dict) -> list[dict]:
    return [i for i in _items(definition) if i.get("natural_key")]


def item_problem(item: dict) -> str | None:
    """Why a question can't be part of the record key, or None if it can."""
    if item.get("calculation"):
        return "is calculated, so it can't be part of the natural key."
    if item.get("conditions"):
        return "only shows when another answer asks for it, so it can't be part of the natural key."
    t = (item.get("input") or {}).get("type")
    if t not in ANSWER_KINDS:
        return f"is a {t.replace('_', ' ')} question, so it can't be part of the natural key. Use text, number, date or single choice."
    return None


def record_key(definition: dict, answers: dict) -> str | None:
    items = record_key_items(definition)
    if not items:
        return None
    parts = []
    for i in items:
        kind = _kind_of_item(i)
        part = normalize(kind, (answers or {}).get(i["key"])) if kind else None
        if part is None:
            return None
        parts.append(part)
    return joined(parts)


def record_key_values(definition: dict, answers: dict) -> dict | None:
    items = record_key_items(definition)
    if not items:
        return None
    return {i["key"]: (answers or {}).get(i["key"]) for i in items}


def record_lookup_parts(definition: dict) -> list[tuple[str, str, str | None]]:
    return [(i["key"], i["label"], _kind_of_item(i)) for i in record_key_items(definition)]


def check_record_key(db: Session, form_id, latest: dict, own: dict, answers: dict, record_id=None) -> str | None:
    """The key for a record being saved, checked.

    latest: the form's latest definition (whose questions make up the key). own: the definition of
    the version the record is saved against. A key question that version has must be answered;
    a record on an older version without that question is saved without a key.
    """
    items = record_key_items(latest)
    if not items:
        return None
    own_keys = {i["key"] for i in _items(own)}
    key = record_key(latest, answers)
    if key is None:
        unanswered = [i for i in items if i["key"] in own_keys and normalize(_kind_of_item(i) or "text", (answers or {}).get(i["key"])) is None]
        if unanswered:
            raise ValidationFailed([field_error(i["key"], "required", f"{i['label']} is part of the form's key, so it is required.")
                                    for i in unanswered])
        return None
    q = select(Record).where(Record.form_id == form_id, Record.natural_key == key)
    if record_id is not None:
        q = q.where(Record.id != record_id)
    other = db.scalars(q.limit(1)).first()
    if other:
        label = " + ".join(i["label"] for i in items)
        raise AppError(409, "duplicate_key", f"Another record already has this {label}.",
                       {"record_id": str(other.id), "key": record_key_values(latest, answers)})
    return key


def reassign_record_keys(db: Session, form_id, latest: dict, apply: bool = True) -> None:
    """When a form version is published: key every record by the new version's key questions.

    Refused when two records would share a key. Records without an answer get no key.
    """
    records = list(db.scalars(select(Record).where(Record.form_id == form_id).order_by(Record.created_at)))
    keys = {r.id: record_key(latest, r.answers) for r in records}
    if record_key_items(latest):
        seen: dict[str, Record] = {}
        clashes = []
        for r in records:
            k = keys[r.id]
            if k is None:
                continue
            if k in seen:
                clashes.append({"record_id": str(r.id), "same_as": str(seen[k].id)})
            else:
                seen[k] = r
        if clashes:
            raise conflict("natural_key_conflict",
                           f"Can't publish this natural key: {len(clashes)} existing record(s) share a key with another record. "
                           "Change them first (archived records count too).", {"duplicates": clashes})
    if apply and any(r.natural_key != keys[r.id] for r in records):
        db.execute(update(Record).where(Record.form_id == form_id).values(natural_key=None))
        db.flush()
        for r in records:
            r.natural_key = keys[r.id]
        db.flush()
