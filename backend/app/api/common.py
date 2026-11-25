from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Atom, ChangeRequest, Form
from app.services import change_requests as cr_service
from app.services import relations as rel_service


def enrich_change_request(db: Session, cr: ChangeRequest, detail: bool = False) -> dict:
    names = cr_service.user_names(db, [cr.submitted_by, cr.reviewed_by])
    d = cr_service.to_dict(cr, names)
    atom = db.get(Atom, cr.atom_id) if cr.atom_id else None
    d["atom_name"] = (atom.values.get("name") if atom else None) or (cr.proposed_values or {}).get("name")
    d["atom_status"] = atom.status if atom else None
    d["stale"] = bool(atom and cr.status == "pending" and cr.base_revision is not None and atom.revision != cr.base_revision)
    if cr.form_id:
        from app.services import forms as form_service

        form = db.get(Form, cr.form_id)
        d["form_name"] = form.name if form else None
        d["form_key"] = form.key if form else None
        d["form_status"] = form.status if form else None
        d["stale"] = bool(form and cr.status == "pending" and cr.base_revision is not None and form.draft_revision != cr.base_revision)
        if detail:
            d["current_revision"] = form.draft_revision if form else None
            d["form_preview"] = form_service.request_preview(db, cr)
        return d
    if detail:
        d["current_values"] = atom.values if atom else None
        d["current_revision"] = atom.revision if atom else None
        rels = cr.proposed_relations or {}
        target_ids = []
        for a in rels.get("add") or []:
            try:
                target_ids.append(uuid.UUID(a["target_atom_id"]))
            except (KeyError, ValueError):
                pass
        target_names = rel_service.atom_names(db, target_ids)
        d["relation_targets"] = {str(k): v for k, v in target_names.items()}
        removed = []
        for rid in rels.get("remove") or []:
            from app.models import AtomRelation

            rel = db.get(AtomRelation, uuid.UUID(rid))
            if rel:
                other_names = rel_service.atom_names(db, [rel.source_atom_id, rel.target_atom_id])
                removed.append(rel_service.relation_to_dict(rel, perspective=cr.atom_id, names=other_names))
        d["relations_removed"] = removed
    return d
