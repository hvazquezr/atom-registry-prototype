"""Reading, searching and writing atoms. Writes only happen through approved change requests."""

from __future__ import annotations

import datetime as dt
import uuid

import re2
from sqlalchemy import Float, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.errors import bad_request, not_found
from app.models import Atom, AtomRevision, ChangeRequest, SchemaVersion
from app.services import natural_keys
from app.services import relations as rel_service
from app.services.fields import get_schema, latest_schema
from app.services.validation import recompute_time_dependent

SYSTEM_SORT = {"created_at": Atom.created_at, "updated_at": Atom.updated_at, "revision": Atom.revision}
FILTER_OPS = ("eq", "ne", "in", "gt", "gte", "lt", "lte", "contains", "exists")
_WORD = re2.compile(r"[\w-]+")


class SchemaCache:
    """Avoids reloading the same schema version while serialising many atoms."""

    def __init__(self, db: Session):
        self.db = db
        self._cache: dict[int, list[dict]] = {}

    def fields(self, version: int) -> list[dict]:
        if version not in self._cache:
            self._cache[version] = get_schema(self.db, version).fields
        return self._cache[version]


def search_text(fields: list[dict], values: dict) -> str:
    parts: list[str] = []
    for f in fields:
        v = values.get(f["key"])
        if v is None:
            continue
        if f["type"] == "text":
            parts.append(v)
        elif f["type"] == "options":
            labels = {c["value"]: c.get("label") or c["value"] for c in (f.get("config") or {}).get("choices", [])}
            for item in v if isinstance(v, list) else [v]:
                parts.extend([item, labels.get(item, "")])
        elif f["type"] == "list" and (f.get("config") or {}).get("item_type", "text") == "text":
            parts.extend(v)
    return " ".join(p for p in parts if p)


def set_search_vector(db: Session, atom: Atom, fields: list[dict]) -> None:
    atom.search_vector = func.to_tsvector("simple", search_text(fields, atom.values))


def serialize(atom: Atom, cache: SchemaCache, now: dt.datetime | None = None) -> dict:
    fields = cache.fields(atom.schema_version)
    values = recompute_time_dependent(fields, atom.values, now=now)
    return {
        "id": str(atom.id),
        "natural_key": natural_keys.atom_key_values(fields, values),
        "schema_version": atom.schema_version,
        "status": atom.status,
        "revision": atom.revision,
        "values": values,
        "created_by": str(atom.created_by) if atom.created_by else None,
        "updated_by": str(atom.updated_by) if atom.updated_by else None,
        "created_at": atom.created_at.isoformat() if atom.created_at else None,
        "updated_at": atom.updated_at.isoformat() if atom.updated_at else None,
    }


def get_atom(db: Session, atom_id: uuid.UUID) -> Atom:
    atom = db.get(Atom, atom_id)
    if not atom:
        raise not_found("Atom")
    return atom


def get_by_key(db: Session, path_value: str | None, query: dict) -> Atom:
    """Find an atom by its natural key: `path_value` for a one-field key, else one query parameter per field."""
    fields = latest_schema(db).fields if latest_schema(db) else []
    key = natural_keys.lookup_key(natural_keys.atom_lookup_parts(fields), path_value, query, "The atom schema")
    atom = db.scalars(select(Atom).where(Atom.natural_key == key)).first()
    if not atom:
        raise not_found("Atom")
    return atom


def pending_requests(db: Session, atom_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count()).select_from(ChangeRequest).where(ChangeRequest.atom_id == atom_id, ChangeRequest.status == "pending")
    ) or 0


# --- Search -----------------------------------------------------------------------------


def _tsquery(q: str) -> str | None:
    words = _WORD.findall(q.lower())[:10]
    if not words:
        return None
    return " & ".join(f"{w.replace(chr(39), '')}:*" for w in words)


def _field_expr(field: dict, as_type: str | None = None):
    raw = Atom.values[field["key"]].astext
    t = as_type or field["type"]
    if t == "number":
        return cast(raw, Float)
    if t == "date":
        return func.to_date(raw, "YYYY-MM-DD")
    return raw


def _coerce(field: dict, value: str):
    t = field["type"]
    try:
        if t == "number":
            return float(value)
        if t == "date":
            return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise bad_request("bad_filter", f"{value!r} is not a valid {t} for {field['key']}.") from exc
    return value


def _filter_clause(field: dict, op: str, value: str):
    key = field["key"]
    t = field["type"]
    is_array = t == "list" or (t == "options" and (field.get("config") or {}).get("multiple"))
    if op == "exists":
        present = Atom.values.has_key(key)  # noqa: W601 - SQLAlchemy JSONB operator
        return present if value.lower() in ("1", "true", "yes", "") else ~present
    if is_array:
        if op in ("eq", "contains"):
            item = _coerce({"type": (field.get("config") or {}).get("item_type", "text")} if t == "list" else {"type": "text"}, value)
            return Atom.values.contains(cast({key: [item]}, JSONB))
        if op == "in":
            return or_(*[Atom.values.contains(cast({key: [v]}, JSONB)) for v in value.split(",")])
        raise bad_request("bad_filter", f"{op} can't be used on {key}; use eq, in, contains or exists.")
    if op == "eq":
        if t in ("text", "options"):
            return Atom.values.contains(cast({key: value}, JSONB))  # uses the GIN index
        return _field_expr(field) == _coerce(field, value)
    if op == "ne":
        return or_(~Atom.values.has_key(key), _field_expr(field) != _coerce(field, value))
    if op == "in":
        return _field_expr(field).in_([_coerce(field, v) for v in value.split(",")])
    if op == "contains":
        if t not in ("text",):
            raise bad_request("bad_filter", f"contains only works on text fields and lists, not {key}.")
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return _field_expr(field).ilike(f"%{escaped}%")
    cmp = {"gt": "__gt__", "gte": "__ge__", "lt": "__lt__", "lte": "__le__"}[op]
    if t == "text" or t == "options":
        return getattr(_field_expr(field), cmp)(value)
    return getattr(_field_expr(field), cmp)(_coerce(field, value))


def search(
    db: Session,
    q: str | None = None,
    status: str | None = "active",
    filters: list[tuple[str, str, str]] | None = None,
    sort: str = "-updated_at",
    page: int = 1,
    page_size: int = 25,
    ids: list[uuid.UUID] | None = None,
) -> dict:
    schema = latest_schema(db)
    fields = {f["key"]: f for f in (schema.fields if schema else [])}
    query = select(Atom)
    conditions = []
    if status and status != "all":
        conditions.append(Atom.status == status)
    if ids:
        conditions.append(Atom.id.in_(ids))
    if q and (tsq := _tsquery(q)):
        conditions.append(Atom.search_vector.op("@@")(func.to_tsquery("simple", tsq)))
    for key, op, value in filters or []:
        if op not in FILTER_OPS:
            raise bad_request("bad_filter", f"Unknown filter operator {op!r}. Use one of {', '.join(FILTER_OPS)}.")
        if key not in fields:
            raise bad_request("bad_filter", f"Unknown field {key!r}.")
        conditions.append(_filter_clause(fields[key], op, value))
    if conditions:
        query = query.where(and_(*conditions))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    desc = sort.startswith("-")
    sort_key = sort.lstrip("-+")
    if sort_key in SYSTEM_SORT:
        order = SYSTEM_SORT[sort_key]
    elif sort_key in fields:
        order = _field_expr(fields[sort_key]) if fields[sort_key]["type"] in ("number", "date", "text") else Atom.values[sort_key].astext
    else:
        raise bad_request("bad_sort", f"Can't sort by {sort_key!r}.")
    order = order.desc().nulls_last() if desc else order.asc().nulls_last()
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    rows = db.scalars(query.order_by(order, Atom.id).offset((page - 1) * page_size).limit(page_size)).all()
    cache = SchemaCache(db)
    return {"items": [serialize(a, cache) for a in rows], "total": total, "page": page, "page_size": page_size}


# --- Writing (called when a change request is approved) --------------------------------


def snapshot_relations(db: Session, atom_id: uuid.UUID) -> list[dict]:
    return [rel_service.relation_to_dict(r, perspective=atom_id) for r in rel_service.relations_for(db, atom_id)]


def write_revision(db: Session, atom: Atom, user_id, change_request_id=None, summary: str = "") -> AtomRevision:
    db.flush()
    rev = AtomRevision(
        atom_id=atom.id, revision=atom.revision, schema_version=atom.schema_version, values=atom.values,
        status=atom.status, relations=snapshot_relations(db, atom.id), change_request_id=change_request_id,
        summary=summary, changed_by=user_id,
    )
    db.add(rev)
    db.flush()
    return rev


def revisions(db: Session, atom_id: uuid.UUID) -> list[AtomRevision]:
    get_atom(db, atom_id)
    return list(db.scalars(select(AtomRevision).where(AtomRevision.atom_id == atom_id).order_by(AtomRevision.revision.desc())))


def recalculate_all(db: Session, schema: SchemaVersion, user_id=None) -> int:
    """After publishing: move atoms to the new schema and recompute their calculated fields.

    Atoms whose values change get a new revision. Returns how many changed.
    """
    fields = schema.fields
    calculated = {f["key"] for f in fields if f.get("calculated")}
    changed = 0
    for atom in db.scalars(select(Atom).where(Atom.schema_version != schema.version)).all():
        base = {k: v for k, v in atom.values.items() if k not in calculated}
        from app.services.expressions import compute

        new_values, _ = compute(fields, base)
        new_values = {k: v for k, v in new_values.items() if v is not None}
        atom.schema_version = schema.version
        if new_values != atom.values:
            atom.values = new_values
            atom.revision += 1
            set_search_vector(db, atom, fields)
            write_revision(db, atom, user_id, summary=f"Recalculated for schema version {schema.version}")
            changed += 1
    db.flush()
    return changed


def refresh_time_dependent(db: Session) -> int:
    """Refresh the stored copy of fields that use today()/now(), so they stay searchable.

    Meant to run daily (see README). Does not create revisions, since nobody changed the atom.
    """
    cache = SchemaCache(db)
    count = 0
    for atom in db.scalars(select(Atom)).all():
        fields = cache.fields(atom.schema_version)
        new_values = recompute_time_dependent(fields, atom.values)
        if new_values != atom.values:
            atom.values = new_values
            set_search_vector(db, atom, fields)
            count += 1
    db.flush()
    return count
