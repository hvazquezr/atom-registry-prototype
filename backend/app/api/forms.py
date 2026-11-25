"""Forms API (part of /api/v1): build forms from atoms, publish them through review, and check answers."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.api.common import enrich_change_request
from app.auth import Principal, require
from app.db import get_db
from app.errors import bad_request
from app.schemas import FormAnswers, FormCreate, FormDraft, FormInputSettings, FormPublish, FormStatusChange
from app.services import change_requests as cr_service
from app.services import forms as form_service

router = APIRouter(prefix="/api/v1", tags=["forms"])
read = require("forms:read")
write = require("forms:write")


def _detail(db: Session, form, response: Response | None = None) -> dict:
    definition, issues = form_service.resolve(db, form)
    versions = form_service.list_versions(db, form)
    names = cr_service.user_names(db, [v.published_by for v in versions] + [v.approved_by for v in versions])
    latest = versions[0] if versions else None
    pending = form_service.pending_request(db, form)
    body = form_service.summary(form)
    body.update({
        "draft": form_service.draft_body(form),
        "resolved_draft": definition,
        "issues": issues,
        "can_publish": not form_service.has_errors(issues) and form.status == "active" and pending is None,
        "versions": [form_service.version_summary(v, names) for v in versions],
        "atoms_updated": form_service.atoms_updated(db, latest),
        "draft_changes": form_service.diff(latest.definition if latest else None, definition),
        "pending_request": enrich_change_request(db, pending) if pending else None,
    })
    if response is not None:
        response.headers["ETag"] = f'"{form.draft_revision}"'
    return body


@router.get("/forms", summary="Search forms")
def search_forms(
    q: str | None = None,
    status: str = Query("active", description="active, deprecated or all"),
    atom_id: uuid.UUID | None = Query(None, description="Only forms whose draft uses this atom"),
    page: int = 1,
    page_size: int = Query(25, le=100),
    db: Session = Depends(get_db),
    _: Principal = Depends(read),
):
    return form_service.search(db, q=q, status=status, atom_id=atom_id, page=page, page_size=page_size)


@router.post("/forms", status_code=201, summary="Create a form (its draft can be edited freely; publishing is reviewed)")
def create_form(body: FormCreate, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    form = form_service.create_form(
        db, p.user, body.key, body.name, body.description,
        [s.model_dump(mode="json") for s in body.sections] if body.sections is not None else None,
    )
    db.commit()
    response.headers["Location"] = f"/api/v1/forms/{form.id}"
    return _detail(db, form, response)


@router.get("/forms/{form_id}", summary="A form with its draft, publish checks and version list (id or key)")
def get_form(form_id: str, response: Response, db: Session = Depends(get_db), _: Principal = Depends(read)):
    return _detail(db, form_service.get_form(db, form_id), response)


def _base(if_match: str | None, body_value: int | None) -> int | None:
    if body_value is not None:
        return body_value
    if if_match:
        try:
            return int(if_match.strip().strip("W/").strip('"'))
        except ValueError as exc:
            raise bad_request("bad_if_match", "If-Match must be the form's draft revision.") from exc
    return None


@router.put("/forms/{form_id}/draft", summary="Save the draft: name, description and all sections and items")
def save_draft(form_id: str, body: FormDraft, response: Response, if_match: str | None = Header(None),
               db: Session = Depends(get_db), p: Principal = Depends(write)):
    form = form_service.get_form(db, form_id)
    data = body.model_dump(mode="json", exclude_unset=True)
    data["base_revision"] = _base(if_match, body.base_revision)
    form = form_service.save_draft(db, p.user, form, data)
    db.commit()
    return _detail(db, form, response)


def _submitted(db: Session, cr, response: Response) -> dict:
    db.commit()
    response.status_code = 202
    response.headers["Location"] = f"/api/v1/change-requests/{cr.id}"
    return enrich_change_request(db, cr)


@router.post("/forms/{form_id}/publish", status_code=202, summary="Submit the draft for review as the next version")
def publish_form(form_id: str, body: FormPublish, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    form = form_service.get_form(db, form_id)
    return _submitted(db, form_service.submit_request(db, p.user, form, "publish_form", body.notes, body.comment), response)


@router.post("/forms/{form_id}/deprecate", status_code=202, summary="Propose deprecating a form")
def deprecate_form(form_id: str, body: FormStatusChange, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    form = form_service.get_form(db, form_id)
    return _submitted(db, form_service.submit_request(db, p.user, form, "deprecate_form", comment=body.comment), response)


@router.post("/forms/{form_id}/restore", status_code=202, summary="Propose restoring a deprecated form")
def restore_form(form_id: str, body: FormStatusChange, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    form = form_service.get_form(db, form_id)
    return _submitted(db, form_service.submit_request(db, p.user, form, "restore_form", comment=body.comment), response)


@router.get("/forms/{form_id}/versions", summary="Published versions, newest first")
def form_versions(form_id: str, db: Session = Depends(get_db), _: Principal = Depends(read)):
    form = form_service.get_form(db, form_id)
    versions = form_service.list_versions(db, form)
    names = cr_service.user_names(db, [v.published_by for v in versions] + [v.approved_by for v in versions])
    return [form_service.version_summary(v, names) for v in versions]


@router.get(
    "/forms/{form_id}/versions/{version}",
    summary="The resolved form, ready to render",
    description=(
        "`version` is a number, `latest` or `draft`. Each item carries its answer key, label, help text, "
        "`requirement` (required, optional, required_if or calculated), "
        "input type and rules, the pinned atom revision and values, its Required if conditions, and the "
        "item it is grouped under (Includes), and `natural_key` (true for the questions that identify a record). "
        "The top-level `natural_key` lists those questions in order."
    ),
)
def form_version(form_id: str, version: str, db: Session = Depends(get_db), _: Principal = Depends(read)):
    form = form_service.get_form(db, form_id)
    if version == "draft":
        definition, issues = form_service.resolve(db, form)
        return dict(definition, version="draft", draft_revision=form.draft_revision, issues=issues)
    fv = form_service.get_version(db, form, version)
    return dict(form_service.with_requirements(dict(fv.definition)), status=form.status, notes=fv.notes)


@router.post(
    "/forms/{form_id}/validate",
    summary="Check a set of answers",
    description=(
        "Returns which questions are visible and required for these answers (Required if), the cleaned "
        "answers and every error. Nothing is stored. Answers to hidden questions are listed under `ignored`."
    ),
)
def validate_answers(form_id: str, body: FormAnswers, db: Session = Depends(get_db), _: Principal = Depends(read)):
    form = form_service.get_form(db, form_id)
    if body.version == "draft":
        definition, _issues = form_service.resolve(db, form)
        version = "draft"
    else:
        fv = form_service.get_version(db, form, body.version)
        definition, version = fv.definition, fv.version
    return dict(form_service.validate_answers(definition, body.answers), version=version)


@router.get("/forms/{form_id}/change-requests", summary="Publish, deprecate and restore requests for a form")
def form_requests(form_id: str, db: Session = Depends(get_db), _: Principal = Depends(read)):
    form = form_service.get_form(db, form_id)
    return [enrich_change_request(db, cr) for cr in cr_service.list_requests(db, status="all", form_id=form.id)]


@router.get("/form-input-settings", summary="How atom values turn into form inputs")
def get_input_settings(db: Session = Depends(get_db), _: Principal = Depends(read)):
    return form_service.input_settings(db)


@router.put("/form-input-settings", summary="Change how atom values turn into form inputs (schema admins)")
def put_input_settings(body: FormInputSettings, db: Session = Depends(get_db), _: Principal = Depends(require("schema:admin"))):
    out = form_service.save_input_settings(db, body.model_dump(exclude_unset=True))
    db.commit()
    return out
