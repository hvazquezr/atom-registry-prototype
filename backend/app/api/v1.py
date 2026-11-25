"""External API, version 1. Stable contract for other systems."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.common import enrich_change_request
from app.auth import Principal, require
from app.db import get_db
from app.errors import ValidationFailed, bad_request
from app.schemas import AtomCreate, AtomPatch, AtomPreview, ChangeRequestCreate, ChangeRequestRevise, StatusChange
from app.services import atoms as atom_service
from app.services import change_requests as cr_service
from app.services import formulas
from app.services import relations as rel_service
from app.services.fields import get_schema, require_schema
from app.services.validation import validate_atom_values

router = APIRouter(prefix="/api/v1", tags=["v1"])
read = require("atoms:read")
write = require("atoms:write")

FILTER_PARAM = re.compile(r"^filter\[([a-z][a-z0-9_]*)\](?:\[([a-z_]+)\])?$")


def _schema_body(db: Session, version: int | None = None) -> dict:
    s = get_schema(db, version) if version else require_schema(db)
    return {
        "version": s.version,
        "published_at": s.published_at.isoformat(),
        "notes": s.notes,
        "fields": s.fields,
        "relation_types": [rel_service.relation_type_to_dict(r) for r in rel_service.list_relation_types(db, include_deprecated=False)],
    }


@router.get("/schema", summary="Current published schema (fields and relation types)")
def current_schema(db: Session = Depends(get_db), _: Principal = Depends(read)):
    return _schema_body(db)


@router.get("/schema/{version}", summary="A specific schema version")
def schema_version(version: int, db: Session = Depends(get_db), _: Principal = Depends(read)):
    return _schema_body(db, version)


@router.get("/relation-types")
def relation_types(db: Session = Depends(get_db), _: Principal = Depends(read)):
    return [rel_service.relation_type_to_dict(r) for r in rel_service.list_relation_types(db)]


# --- Atoms ------------------------------------------------------------------------------


@router.get(
    "/atoms",
    summary="Search atoms",
    description=(
        "Text search with `q` (prefix match on text and option fields) and field filters as "
        "`filter[<field>][<op>]=<value>` or `filter[<field>]=<value>` (eq). Operators: eq, ne, in "
        "(comma-separated), gt, gte, lt, lte, contains, exists. Sort with `sort=<field>` or "
        "`sort=-<field>`; system sorts are created_at, updated_at, revision."
    ),
)
def search_atoms(
    request: Request,
    q: str | None = None,
    status: str = Query("active", description="active, deprecated or all"),
    sort: str = "-updated_at",
    page: int = 1,
    page_size: int = Query(25, le=100),
    ids: str | None = Query(None, description="Comma-separated atom ids"),
    db: Session = Depends(get_db),
    _: Principal = Depends(read),
):
    filters = []
    for name, value in request.query_params.multi_items():
        if name.startswith("filter"):
            m = FILTER_PARAM.match(name)
            if not m:
                raise bad_request("bad_filter", f"Can't read filter {name!r}. Use filter[field][op]=value.")
            filters.append((m.group(1), m.group(2) or "eq", value))
    id_list = None
    if ids:
        try:
            id_list = [uuid.UUID(i) for i in ids.split(",") if i]
        except ValueError as exc:
            raise bad_request("bad_ids", "ids must be comma-separated UUIDs.") from exc
    return atom_service.search(db, q=q, status=status, filters=filters, sort=sort, page=page, page_size=page_size, ids=id_list)


@router.get("/atoms/{atom_id}")
def get_atom(atom_id: uuid.UUID, response: Response, db: Session = Depends(get_db), _: Principal = Depends(read)):
    atom = atom_service.get_atom(db, atom_id)
    body = atom_service.serialize(atom, atom_service.SchemaCache(db))
    body["pending_change_requests"] = atom_service.pending_requests(db, atom_id)
    response.headers["ETag"] = f'"{atom.revision}"'
    return body


@router.get("/atoms/{atom_id}/revisions")
def atom_revisions(atom_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(read)):
    revs = atom_service.revisions(db, atom_id)
    names = cr_service.user_names(db, [r.changed_by for r in revs])
    return [
        {
            "revision": r.revision, "schema_version": r.schema_version, "values": r.values, "status": r.status,
            "relations": r.relations, "summary": r.summary,
            "change_request_id": str(r.change_request_id) if r.change_request_id else None,
            "changed_by": str(r.changed_by) if r.changed_by else None, "changed_by_name": names.get(r.changed_by),
            "changed_at": r.changed_at.isoformat(),
        }
        for r in revs
    ]


@router.get("/atoms/{atom_id}/relations")
def atom_relations(
    atom_id: uuid.UUID,
    type: str | None = None,
    direction: str = Query("both", pattern="^(out|in|both)$"),
    db: Session = Depends(get_db),
    _: Principal = Depends(read),
):
    atom_service.get_atom(db, atom_id)
    rels = rel_service.relations_for(db, atom_id, type, direction)
    names = rel_service.atom_names(db, [r.source_atom_id for r in rels] + [r.target_atom_id for r in rels])
    return [rel_service.relation_to_dict(r, perspective=atom_id, names=names) for r in rels]


@router.get("/atoms/{atom_id}/graph", summary="Atoms reachable through relations, up to `depth` hops")
def atom_graph(
    atom_id: uuid.UUID,
    types: str | None = Query(None, description="Comma-separated relation type keys"),
    depth: int = Query(2, ge=1, le=10),
    direction: str = Query("both", pattern="^(out|in|both)$"),
    db: Session = Depends(get_db),
    _: Principal = Depends(read),
):
    atom_service.get_atom(db, atom_id)
    type_list = [t for t in (types or "").split(",") if t] or None
    return rel_service.graph(db, atom_id, type_list, depth, direction)


def _revision_from(if_match: str | None, body_value: int | None) -> int | None:
    if body_value is not None:
        return body_value
    if if_match:
        try:
            return int(if_match.strip().strip('W/').strip('"'))
        except ValueError as exc:
            raise bad_request("bad_if_match", "If-Match must be the atom's revision number.") from exc
    return None


def _created(db: Session, cr, response: Response) -> dict:
    db.commit()
    response.status_code = 202
    response.headers["Location"] = f"/api/v1/change-requests/{cr.id}"
    return enrich_change_request(db, cr)


@router.post("/atoms/preview", summary="Validate values and preview calculated fields without saving anything")
def preview_atom(body: AtomPreview, db: Session = Depends(get_db), _: Principal = Depends(read)):
    schema = require_schema(db)
    existing = atom_service.get_atom(db, body.atom_id).values if body.atom_id else None
    values, errors = None, []
    try:
        values = validate_atom_values(schema.fields, body.values, existing=existing)
    except ValidationFailed as exc:
        errors = exc.errors
    # The formula is checked on its own, so its problems show up while other fields are still empty.
    merged = dict(existing or {}) | dict(body.values)
    try:
        formulas.check(db, body.atom_id, merged)
    except ValidationFailed as exc:
        errors = errors + exc.errors
    try:
        inputs = formulas.inputs(db, body.atom_id, merged)
    except formulas.FormulaError:
        inputs = []
    return {"valid": not errors, "values": values if not errors else None, "errors": errors, "formula_inputs": inputs}


@router.post("/atoms", status_code=202, summary="Propose a new atom (opens a change request)")
def create_atom(body: AtomCreate, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    cr = cr_service.submit(
        db, p.user, "create", values=body.values,
        relations={"add": [r.model_dump(mode="json") for r in body.relations]}, comment=body.comment,
    )
    return _created(db, cr, response)


@router.patch("/atoms/{atom_id}", status_code=202, summary="Propose changes to an atom (opens a change request)")
def update_atom(
    atom_id: uuid.UUID, body: AtomPatch, response: Response,
    if_match: str | None = Header(None), db: Session = Depends(get_db), p: Principal = Depends(write),
):
    cr = cr_service.submit(
        db, p.user, "update", atom_id=atom_id, values=body.values, relations=body.relations.model_dump(mode="json"),
        base_revision=_revision_from(if_match, body.base_revision), comment=body.comment,
    )
    return _created(db, cr, response)


@router.post("/atoms/{atom_id}/deprecate", status_code=202, summary="Propose deprecating an atom")
def deprecate_atom(atom_id: uuid.UUID, body: StatusChange, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    cr = cr_service.submit(db, p.user, "deprecate", atom_id=atom_id, base_revision=body.base_revision, comment=body.comment)
    return _created(db, cr, response)


@router.post("/atoms/{atom_id}/restore", status_code=202, summary="Propose restoring a deprecated atom")
def restore_atom(atom_id: uuid.UUID, body: StatusChange, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    cr = cr_service.submit(db, p.user, "restore", atom_id=atom_id, base_revision=body.base_revision, comment=body.comment)
    return _created(db, cr, response)


# --- Change requests --------------------------------------------------------------------


@router.post("/change-requests", status_code=202)
def create_change_request(body: ChangeRequestCreate, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    cr = cr_service.submit(
        db, p.user, body.kind, atom_id=body.atom_id, values=body.values,
        relations=body.relations.model_dump(mode="json"), base_revision=body.base_revision, comment=body.comment,
    )
    return _created(db, cr, response)


@router.get("/change-requests")
def list_change_requests(
    status: str | None = None,
    atom_id: uuid.UUID | None = None,
    mine: bool = False,
    db: Session = Depends(get_db),
    p: Principal = Depends(read),
):
    rows = cr_service.list_requests(db, status=status, atom_id=atom_id, submitted_by=p.id if mine else None)
    return [enrich_change_request(db, cr) for cr in rows]


@router.get("/change-requests/{cr_id}")
def get_change_request(cr_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(read)):
    return enrich_change_request(db, cr_service.get(db, cr_id), detail=True)


@router.patch("/change-requests/{cr_id}", summary="Edit your pending change request")
def revise_change_request(cr_id: uuid.UUID, body: ChangeRequestRevise, db: Session = Depends(get_db), p: Principal = Depends(write)):
    cr = cr_service.revise(
        db, p.user, cr_service.get(db, cr_id), body.values,
        body.relations.model_dump(mode="json") if body.relations else None, body.comment,
    )
    db.commit()
    return enrich_change_request(db, cr, detail=True)


@router.post("/change-requests/{cr_id}/withdraw")
def withdraw_change_request(cr_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(write)):
    cr = cr_service.withdraw(db, p.user, cr_service.get(db, cr_id))
    db.commit()
    return enrich_change_request(db, cr)
