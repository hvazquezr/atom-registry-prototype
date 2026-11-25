"""Change requests: every create, edit, relation change or deprecation is reviewed first."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ValidationFailed, bad_request, conflict, field_error, not_found
from app.models import Atom, AtomRelation, ChangeRequest, User
from app.services import atoms as atom_service
from app.services import relations as rel_service
from app.services.fields import require_schema
from app.services.validation import validate_atom_values

KINDS = ("create", "update", "deprecate", "restore")


def to_dict(cr: ChangeRequest, names: dict | None = None) -> dict:
    return {
        "id": str(cr.id), "atom_id": str(cr.atom_id) if cr.atom_id else None,
        "form_id": str(cr.form_id) if cr.form_id else None, "kind": cr.kind,
        "proposed_values": cr.proposed_values or {}, "proposed_relations": cr.proposed_relations or {},
        "base_revision": cr.base_revision, "comment": cr.comment, "status": cr.status,
        "submitted_by": str(cr.submitted_by) if cr.submitted_by else None,
        "submitted_by_name": (names or {}).get(cr.submitted_by),
        "submitted_at": cr.submitted_at.isoformat() if cr.submitted_at else None,
        "reviewed_by": str(cr.reviewed_by) if cr.reviewed_by else None,
        "reviewed_by_name": (names or {}).get(cr.reviewed_by),
        "reviewed_at": cr.reviewed_at.isoformat() if cr.reviewed_at else None,
        "review_comment": cr.review_comment,
    }


def user_names(db: Session, ids) -> dict:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: (u.display_name or u.username) for u in db.scalars(select(User).where(User.id.in_(ids)))}


def get(db: Session, cr_id: uuid.UUID) -> ChangeRequest:
    cr = db.get(ChangeRequest, cr_id)
    if not cr:
        raise not_found("Change request")
    return cr


def _normalize_relations(raw: dict | None) -> dict:
    raw = raw or {}
    adds = []
    for item in raw.get("add") or []:
        if not isinstance(item, dict) or not item.get("type") or not item.get("target_atom_id"):
            raise bad_request("bad_relation", "Each relation to add needs a type and a target_atom_id.")
        direction = item.get("direction", "out")
        if direction not in ("out", "in"):
            raise bad_request("bad_relation", "direction must be out or in.")
        adds.append({"type": item["type"], "target_atom_id": str(item["target_atom_id"]), "direction": direction,
                     "note": (item.get("note") or "").strip(), "attributes": item.get("attributes") or {}})
    removes = [str(r) for r in raw.get("remove") or []]
    return {"add": adds, "remove": removes}


def _check_relations(db: Session, atom_id: uuid.UUID | None, rels: dict) -> list[dict]:
    """Validate proposed relation changes. Returns the adds with clean attributes."""
    errors = []
    clean = []
    for i, item in enumerate(rels["add"]):
        where = f"relations.add[{i}]"
        try:
            rt = rel_service.get_relation_type(db, item["type"])
        except AppError as exc:
            errors.append(field_error(where, "unknown_type", exc.message))
            continue
        try:
            target = db.get(Atom, uuid.UUID(item["target_atom_id"]))
        except ValueError:
            target = None
        if not target:
            errors.append(field_error(where, "unknown_atom", "The related atom does not exist."))
            continue
        if target.status != "active":
            errors.append(field_error(where, "inactive_atom", "The related atom is deprecated."))
        if rt.require_note and not item.get("note"):
            errors.append(field_error(where, "note_required", f"A {rt.label!r} relation needs a note."))
        try:
            attrs = rel_service.validate_attributes(rt, item["attributes"])
        except ValidationFailed as exc:
            errors.extend(exc.errors)
            continue
        if atom_id is not None:
            src, tgt = (atom_id, target.id) if item["direction"] == "out" else (target.id, atom_id)
            try:
                rel_service.check_link(db, rt, src, tgt, ignore={uuid.UUID(r) for r in rels["remove"]})
            except AppError as exc:
                errors.append(field_error(where, exc.code, exc.message))
        clean.append(dict(item, attributes=attrs))
    for rid in rels["remove"]:
        try:
            rel = db.get(AtomRelation, uuid.UUID(rid))
        except ValueError:
            rel = None
        if not rel or atom_id not in (rel.source_atom_id, rel.target_atom_id):
            errors.append(field_error("relations.remove", "unknown_relation", f"Relation {rid} is not linked to this atom."))
    if errors:
        raise ValidationFailed(errors)
    return clean


def _validate(db: Session, kind: str, atom: Atom | None, values: dict, rels: dict) -> dict:
    schema = require_schema(db)
    if kind == "create":
        validate_atom_values(schema.fields, values)
    elif kind == "update":
        if not values and not rels["add"] and not rels["remove"]:
            raise bad_request("empty_change", "The change request doesn't change anything.")
        validate_atom_values(schema.fields, values, existing=atom.values)
    elif values or rels["add"] or rels["remove"]:
        raise bad_request("bad_change", f"A {kind} request can't change values or relations.")
    rels["add"] = _check_relations(db, atom.id if atom else None, rels)
    return rels


def submit(db: Session, user: User, kind: str, atom_id: uuid.UUID | None = None, values: dict | None = None,
           relations: dict | None = None, base_revision: int | None = None, comment: str = "") -> ChangeRequest:
    if kind not in KINDS:
        raise bad_request("bad_kind", f"kind must be one of {', '.join(KINDS)}.")
    values = values or {}
    atom = None
    if kind == "create":
        if atom_id:
            raise bad_request("bad_change", "A create request can't name an existing atom.")
    else:
        if not atom_id:
            raise bad_request("bad_change", f"A {kind} request needs atom_id.")
        atom = atom_service.get_atom(db, atom_id)
        if kind == "deprecate" and atom.status == "deprecated":
            raise conflict("already_deprecated", "The atom is already deprecated.")
        if kind == "restore" and atom.status == "active":
            raise conflict("already_active", "The atom is already active.")
        if kind == "update" and atom.status != "active":
            raise conflict("deprecated", "Restore the atom before changing it.")
        if base_revision is not None and base_revision != atom.revision:
            raise conflict("stale", "The atom changed since you loaded it.", {"current_revision": atom.revision})
    rels = _validate(db, kind, atom, values, _normalize_relations(relations))
    cr = ChangeRequest(
        atom_id=atom.id if atom else None, kind=kind, proposed_values=values, proposed_relations=rels,
        base_revision=atom.revision if atom else None, comment=comment or "", submitted_by=user.id,
    )
    db.add(cr)
    db.flush()
    return cr


def revise(db: Session, user: User, cr: ChangeRequest, values: dict | None, relations: dict | None, comment: str | None) -> ChangeRequest:
    """The submitter edits a pending request. This also moves it onto the atom's current revision."""
    if cr.form_id:
        from app.services import forms as form_service

        notes = (values or {}).get("notes") if values is not None else None
        return form_service.revise_request(db, user, cr, notes, comment)
    if cr.status != "pending":
        raise conflict("not_pending", "Only pending change requests can be edited.")
    if cr.submitted_by != user.id:
        raise AppError(403, "forbidden", "Only the person who submitted a change request can edit it.")
    atom = atom_service.get_atom(db, cr.atom_id) if cr.atom_id else None
    new_values = cr.proposed_values if values is None else values
    new_rels = _normalize_relations(cr.proposed_relations if relations is None else relations)
    cr.proposed_relations = _validate(db, cr.kind, atom, new_values, new_rels)
    cr.proposed_values = new_values
    if comment is not None:
        cr.comment = comment
    if atom:
        cr.base_revision = atom.revision
    db.flush()
    return cr


def withdraw(db: Session, user: User, cr: ChangeRequest) -> ChangeRequest:
    if cr.status != "pending":
        raise conflict("not_pending", "Only pending change requests can be withdrawn.")
    if cr.submitted_by != user.id and "admin" not in user.roles:
        raise AppError(403, "forbidden", "Only the submitter can withdraw a change request.")
    cr.status = "withdrawn"
    db.flush()
    return cr


def _check_reviewer(user: User, cr: ChangeRequest) -> None:
    if cr.status != "pending":
        raise conflict("not_pending", f"This change request is already {cr.status}.")
    if cr.submitted_by == user.id and not get_settings().allow_self_approval:
        raise AppError(403, "self_review", "You can't review your own change request.")


def reject(db: Session, user: User, cr: ChangeRequest, comment: str = "") -> ChangeRequest:
    _check_reviewer(user, cr)
    cr.status = "rejected"
    cr.reviewed_by = user.id
    cr.reviewed_at = dt.datetime.now(dt.UTC)
    cr.review_comment = comment or ""
    db.flush()
    return cr


def _mark_approved(user: User, cr: ChangeRequest, comment: str) -> None:
    cr.status = "approved"
    cr.reviewed_by = user.id
    cr.reviewed_at = dt.datetime.now(dt.UTC)
    cr.review_comment = comment or ""


def approve(db: Session, user: User, cr: ChangeRequest, comment: str = "") -> tuple[ChangeRequest, Atom | None]:
    """Validate again against the current schema and atom, then apply everything in one transaction.

    Form requests are applied by the forms service and return no atom.
    """
    _check_reviewer(user, cr)
    if cr.form_id:
        from app.services import forms as form_service

        form_service.apply_request(db, user, cr)
        _mark_approved(user, cr, comment)
        db.flush()
        return cr, None
    schema = require_schema(db)
    atom = None
    if cr.atom_id:
        atom = db.execute(select(Atom).where(Atom.id == cr.atom_id).with_for_update()).scalar_one()
        if cr.base_revision is not None and atom.revision != cr.base_revision:
            raise conflict(
                "stale",
                "The atom changed after this request was submitted. The submitter needs to update the request.",
                {"base_revision": cr.base_revision, "current_revision": atom.revision},
            )
    rels = _normalize_relations(cr.proposed_relations)
    summary = cr.kind
    if cr.kind == "create":
        values = validate_atom_values(schema.fields, cr.proposed_values)
        atom = Atom(schema_version=schema.version, values=values, status="active", revision=1,
                    created_by=cr.submitted_by, updated_by=cr.submitted_by)
        db.add(atom)
        db.flush()
        summary = "Created"
    elif cr.kind == "update":
        atom.values = validate_atom_values(schema.fields, cr.proposed_values, existing=atom.values)
        atom.schema_version = schema.version
        atom.revision += 1
        summary = "Updated " + ", ".join(sorted(cr.proposed_values)) if cr.proposed_values else "Updated relations"
    elif cr.kind == "deprecate":
        atom.status = "deprecated"
        atom.revision += 1
        summary = "Deprecated"
    elif cr.kind == "restore":
        atom.status = "active"
        atom.revision += 1
        summary = "Restored"
    atom.updated_by = cr.submitted_by

    # Relations: remove first, so a replacement link doesn't trip cardinality rules.
    for rid in rels["remove"]:
        rel = db.get(AtomRelation, uuid.UUID(rid))
        if rel and atom.id in (rel.source_atom_id, rel.target_atom_id):
            db.delete(rel)
    db.flush()
    for item in rels["add"]:
        rt = rel_service.get_relation_type(db, item["type"])
        target = db.get(Atom, uuid.UUID(item["target_atom_id"]))
        if not target or target.status != "active":
            raise ValidationFailed([field_error("relations", "inactive_atom", "A related atom no longer exists or is deprecated.")])
        attrs = rel_service.validate_attributes(rt, item.get("attributes"))
        src, tgt = (atom.id, target.id) if item["direction"] == "out" else (target.id, atom.id)
        try:
            rel_service.add_link(db, rt, src, tgt, attrs, cr.submitted_by, note=item.get("note") or "")
        except AppError as exc:
            raise ValidationFailed([field_error("relations", exc.code, exc.message)]) from exc
    if (rels["add"] or rels["remove"]) and cr.kind == "update" and not cr.proposed_values:
        summary = "Updated relations"
    elif rels["add"] or rels["remove"]:
        summary += " and relations"

    atom_service.set_search_vector(db, atom, schema.fields)
    atom_service.write_revision(db, atom, cr.submitted_by, change_request_id=cr.id, summary=summary)
    cr.atom_id = atom.id
    cr.status = "approved"
    cr.reviewed_by = user.id
    cr.reviewed_at = dt.datetime.now(dt.UTC)
    cr.review_comment = comment or ""
    db.flush()
    return cr, atom


def list_requests(db: Session, status: str | None = None, atom_id: uuid.UUID | None = None,
                  submitted_by: uuid.UUID | None = None, limit: int = 100, form_id: uuid.UUID | None = None) -> list[ChangeRequest]:
    q = select(ChangeRequest)
    if form_id:
        q = q.where(ChangeRequest.form_id == form_id)
    if status and status != "all":
        q = q.where(ChangeRequest.status == status)
    if atom_id:
        q = q.where(ChangeRequest.atom_id == atom_id)
    if submitted_by:
        q = q.where(ChangeRequest.submitted_by == submitted_by)
    order = ChangeRequest.submitted_at.asc() if status == "pending" else ChangeRequest.submitted_at.desc()
    return list(db.scalars(q.order_by(order).limit(min(limit, 500))))
