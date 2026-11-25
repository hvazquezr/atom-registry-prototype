"""Relation types and typed links between atoms."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ValidationFailed, bad_request, conflict, field_error, not_found
from app.models import Atom, AtomRelation, RelationType
from app.services.fields import KEY_PATTERN, check_config
from app.services.validation import FIELD_TYPES, validate_atom_values

CARDINALITIES = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")


def relation_type_to_dict(rt: RelationType) -> dict:
    return {
        "id": str(rt.id), "key": rt.key, "label": rt.label, "inverse_label": rt.inverse_label,
        "description": rt.description, "directed": rt.directed, "cardinality": rt.cardinality,
        "allow_cycles": rt.allow_cycles, "require_note": rt.require_note, "attributes": rt.attributes or [], "status": rt.status,
    }


def list_relation_types(db: Session, include_deprecated: bool = True) -> list[RelationType]:
    q = select(RelationType).order_by(RelationType.label)
    if not include_deprecated:
        q = q.where(RelationType.status == "active")
    return list(db.scalars(q))


def get_relation_type(db: Session, key_or_id: str) -> RelationType:
    try:
        rt = db.get(RelationType, uuid.UUID(str(key_or_id)))
    except ValueError:
        rt = db.scalars(select(RelationType).where(RelationType.key == key_or_id)).first()
    if not rt:
        raise not_found(f"Relation type {key_or_id!r}")
    return rt


def _check_attribute_defs(defs: list) -> list[dict]:
    errors = []
    keys = set()
    for i, d in enumerate(defs or []):
        where = f"attributes[{i}]"
        if not isinstance(d, dict):
            errors.append(field_error(where, "invalid", "Each attribute must be an object."))
            continue
        if not KEY_PATTERN.fullmatch(d.get("key") or ""):
            errors.append(field_error(where, "invalid", "Attribute keys use lowercase letters, digits and _."))
        if d.get("key") in keys:
            errors.append(field_error(where, "duplicate", f"Attribute {d.get('key')!r} is listed twice."))
        keys.add(d.get("key"))
        if d.get("type") not in FIELD_TYPES:
            errors.append(field_error(where, "invalid", f"Type must be one of {', '.join(FIELD_TYPES)}."))
            continue
        for msg in check_config(d["type"], d.get("config") or {}):
            errors.append(field_error(where, "invalid", msg))
        if d.get("calculated"):
            errors.append(field_error(where, "invalid", "Relation attributes can't be calculated."))
    return errors


def save_relation_type(db: Session, data: dict, rt_id: uuid.UUID | None = None) -> RelationType:
    rt = None
    if rt_id:
        rt = db.get(RelationType, rt_id)
        if not rt:
            raise not_found("Relation type")
        if data.get("key") and data["key"] != rt.key:
            raise conflict("key_frozen", "A relation type's key can't change.")
    else:
        if db.scalars(select(RelationType).where(RelationType.key == data.get("key"))).first():
            raise conflict("duplicate_key", f"A relation type with key {data.get('key')!r} already exists.")
    merged = (relation_type_to_dict(rt) if rt else {"directed": True, "cardinality": "many_to_many", "allow_cycles": True, "require_note": True, "attributes": []}) | data
    errors = []
    if not KEY_PATTERN.fullmatch(merged.get("key") or ""):
        errors.append(field_error("key", "invalid", "Key must start with a letter and use only lowercase letters, digits and _."))
    if not (merged.get("label") or "").strip():
        errors.append(field_error("label", "required", "Label is required."))
    if merged.get("cardinality") not in CARDINALITIES:
        errors.append(field_error("cardinality", "invalid", f"Cardinality must be one of {', '.join(CARDINALITIES)}."))
    if not merged.get("directed") and merged.get("cardinality") in ("one_to_many", "many_to_one"):
        errors.append(field_error("cardinality", "invalid", "Undirected relations can only be one_to_one or many_to_many."))
    errors.extend(_check_attribute_defs(merged.get("attributes")))
    if errors:
        raise ValidationFailed(errors)
    if rt is None:
        rt = RelationType(key=merged["key"])
        db.add(rt)
    rt.label = merged["label"].strip()
    rt.inverse_label = merged.get("inverse_label") or ""
    rt.description = merged.get("description") or ""
    rt.directed = bool(merged["directed"])
    rt.cardinality = merged["cardinality"]
    rt.allow_cycles = bool(merged["allow_cycles"])
    rt.require_note = bool(merged["require_note"])
    rt.attributes = merged.get("attributes") or []
    if "status" in data and data["status"] in ("active", "deprecated"):
        rt.status = data["status"]
    db.flush()
    return rt


# --- Links ------------------------------------------------------------------------------


def validate_attributes(rt: RelationType, attributes: dict | None) -> dict:
    defs = [dict(d, calculated=False) for d in (rt.attributes or [])]
    try:
        return validate_atom_values(defs, attributes or {})
    except ValidationFailed as exc:
        raise ValidationFailed([dict(e, field=f"relation.{rt.key}.{e['field']}") for e in exc.errors]) from exc


def normalize_pair(rt: RelationType, source: uuid.UUID, target: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Undirected relations are stored once, with the smaller id as source."""
    if not rt.directed and str(target) < str(source):
        return target, source
    return source, target


def _path_exists(db: Session, rt: RelationType, start: uuid.UUID, goal: uuid.UUID) -> bool:
    sql = text(
        """
        WITH RECURSIVE walk(atom_id, depth) AS (
            SELECT CAST(:start AS uuid), 0
            UNION
            SELECT r.target_atom_id, w.depth + 1
            FROM walk w JOIN atom_relation r
              ON r.source_atom_id = w.atom_id AND r.relation_type_id = :rt
            WHERE w.depth < 1000
        )
        SELECT 1 FROM walk WHERE atom_id = :goal LIMIT 1
        """
    )
    return db.execute(sql, {"start": str(start), "goal": str(goal), "rt": str(rt.id)}).first() is not None


def check_link(db: Session, rt: RelationType, source: uuid.UUID, target: uuid.UUID, ignore: set[uuid.UUID] | None = None) -> None:
    """Raise if adding source→target would break the type's rules."""
    ignore = ignore or set()
    if rt.status != "active":
        raise conflict("deprecated", f"Relation type {rt.label!r} is deprecated.")
    if source == target:
        raise bad_request("self_link", "An atom can't be related to itself.")
    s, t = normalize_pair(rt, source, target)
    existing = db.scalars(
        select(AtomRelation).where(
            AtomRelation.relation_type_id == rt.id, AtomRelation.source_atom_id == s, AtomRelation.target_atom_id == t
        )
    ).first()
    if existing and existing.id not in ignore:
        raise conflict("duplicate_relation", f"These atoms are already linked as {rt.label!r}.")

    def count(col, atom):
        rows = db.scalars(select(AtomRelation.id).where(AtomRelation.relation_type_id == rt.id, col == atom)).all()
        return len([r for r in rows if r not in ignore])

    card = rt.cardinality
    if rt.directed:
        if card in ("one_to_one", "many_to_one") and count(AtomRelation.source_atom_id, source):
            raise conflict("cardinality", f"An atom can have only one {rt.label!r} link.")
        if card in ("one_to_one", "one_to_many") and count(AtomRelation.target_atom_id, target):
            raise conflict("cardinality", f"An atom can be the target of only one {rt.label!r} link.")
        if not rt.allow_cycles and _path_exists(db, rt, target, source):
            raise conflict("cycle", f"This {rt.label!r} link would create a loop.")
    elif card == "one_to_one":
        for atom in (source, target):
            if count(AtomRelation.source_atom_id, atom) + count(AtomRelation.target_atom_id, atom):
                raise conflict("cardinality", f"An atom can have only one {rt.label!r} link.")


def add_link(db: Session, rt: RelationType, source: uuid.UUID, target: uuid.UUID, attributes: dict, user_id, note: str = "") -> AtomRelation:
    check_link(db, rt, source, target)
    s, t = normalize_pair(rt, source, target)
    rel = AtomRelation(relation_type_id=rt.id, source_atom_id=s, target_atom_id=t, note=note or "", attributes=attributes, created_by=user_id)
    db.add(rel)
    db.flush()
    return rel


def relations_for(db: Session, atom_id: uuid.UUID, type_key: str | None = None, direction: str = "both") -> list[AtomRelation]:
    q = select(AtomRelation)
    if direction == "out":
        q = q.where(AtomRelation.source_atom_id == atom_id)
    elif direction == "in":
        q = q.where(AtomRelation.target_atom_id == atom_id)
    else:
        q = q.where(or_(AtomRelation.source_atom_id == atom_id, AtomRelation.target_atom_id == atom_id))
    if type_key:
        q = q.where(AtomRelation.relation_type_id == get_relation_type(db, type_key).id)
    return list(db.scalars(q.order_by(AtomRelation.created_at)))


def relation_to_dict(rel: AtomRelation, perspective: uuid.UUID | None = None, names: dict | None = None) -> dict:
    rt = rel.relation_type
    d = {
        "id": str(rel.id), "type": rt.key, "type_label": rt.label, "directed": rt.directed,
        "source_atom_id": str(rel.source_atom_id), "target_atom_id": str(rel.target_atom_id),
        "note": rel.note or "", "attributes": rel.attributes or {}, "created_at": rel.created_at.isoformat() if rel.created_at else None,
    }
    if perspective is not None:
        outgoing = rel.source_atom_id == perspective
        other = rel.target_atom_id if outgoing else rel.source_atom_id
        d["direction"] = "out" if (outgoing or not rt.directed) else "in"
        d["label"] = rt.label if d["direction"] == "out" else (rt.inverse_label or rt.label)
        d["other_atom_id"] = str(other)
        if names is not None:
            d["other_atom_name"] = names.get(other)
    return d


def atom_names(db: Session, ids) -> dict:
    ids = list(set(ids))
    if not ids:
        return {}
    rows = db.execute(select(Atom.id, Atom.values["name"].astext).where(Atom.id.in_(ids))).all()
    return {r[0]: r[1] for r in rows}


def graph(db: Session, atom_id: uuid.UUID, types: list[str] | None, depth: int, direction: str = "both") -> dict:
    """Atoms reachable from `atom_id` within `depth` hops, with the edges between them."""
    depth = max(1, min(depth, get_settings().max_graph_depth))
    type_ids = [str(get_relation_type(db, t).id) for t in types] if types else None
    type_filter = "AND r.relation_type_id = ANY(CAST(:types AS uuid[]))" if type_ids else ""
    if direction == "out":
        step = f"JOIN atom_relation r ON r.source_atom_id = w.atom_id {type_filter}"
        next_id = "r.target_atom_id"
    elif direction == "in":
        step = f"JOIN atom_relation r ON r.target_atom_id = w.atom_id {type_filter}"
        next_id = "r.source_atom_id"
    else:
        step = f"JOIN atom_relation r ON (r.source_atom_id = w.atom_id OR r.target_atom_id = w.atom_id) {type_filter}"
        next_id = "CASE WHEN r.source_atom_id = w.atom_id THEN r.target_atom_id ELSE r.source_atom_id END"
    sql = text(
        f"""
        WITH RECURSIVE walk(atom_id, depth, path, edge_id) AS (
            SELECT CAST(:start AS uuid), 0, ARRAY[CAST(:start AS uuid)], CAST(NULL AS uuid)
            UNION ALL
            SELECT {next_id}, w.depth + 1, w.path || {next_id}, r.id
            FROM walk w {step}
            WHERE w.depth < :depth AND NOT ({next_id} = ANY(w.path))
        )
        SELECT atom_id, MIN(depth) AS depth, array_agg(DISTINCT edge_id) FILTER (WHERE edge_id IS NOT NULL)
        FROM walk GROUP BY atom_id
        """
    )
    params = {"start": str(atom_id), "depth": depth}
    if type_ids:
        params["types"] = type_ids
    rows = db.execute(sql, params).all()
    node_depth = {r[0]: r[1] for r in rows}
    node_ids = set(node_depth)
    # every edge among the reached atoms (respecting the type filter)
    q = select(AtomRelation).where(AtomRelation.source_atom_id.in_(node_ids), AtomRelation.target_atom_id.in_(node_ids))
    if type_ids:
        q = q.where(AtomRelation.relation_type_id.in_([uuid.UUID(t) for t in type_ids]))
    edges = list(db.scalars(q))
    names = atom_names(db, node_ids)
    statuses = dict(db.execute(select(Atom.id, Atom.status).where(Atom.id.in_(node_ids))).all())
    return {
        "root": str(atom_id),
        "depth": depth,
        "nodes": [
            {"id": str(n), "name": names.get(n), "status": statuses.get(n), "depth": node_depth[n]}
            for n in sorted(node_ids, key=lambda x: (node_depth[x], names.get(x) or ""))
        ],
        "edges": [relation_to_dict(e) for e in edges],
    }
