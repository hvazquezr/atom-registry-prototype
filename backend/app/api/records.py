"""Records API (part of /api/v1): saved answers to published forms."""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.auth import Principal, require
from app.db import get_db
from app.errors import bad_request, not_found
from app.schemas import RecordAnswers, RecordCreate, RecordPatch, RecordStatusChange
from app.services import forms as form_service
from app.services import records as record_service

router = APIRouter(prefix="/api/v1", tags=["records"])
data_router = APIRouter(prefix="/api/v1", tags=["data"])
read = require("records:read")
write = require("records:write")

FILTER_PARAM = re.compile(r"^filter\[([a-z][a-z0-9_]*)\](?:\[([a-z_]+)\])?$")


def _body(db: Session, rec, response: Response | None = None, **extra) -> dict:
    body = record_service.serialize_many(db, [rec], detail=True)[0]
    body.update(extra)
    if response is not None:
        response.headers["ETag"] = f'"{rec.revision}"'
    return body


def _base(if_match: str | None, body_value: int | None) -> int | None:
    if body_value is not None:
        return body_value
    if if_match:
        try:
            return int(if_match.strip().strip("W/").strip('"'))
        except ValueError as exc:
            raise bad_request("bad_if_match", "If-Match must be the record's revision.") from exc
    return None


def _filters(request: Request) -> list[tuple[str, str, str]]:
    filters = []
    for name, value in request.query_params.multi_items():
        if name.startswith("filter"):
            m = FILTER_PARAM.match(name)
            if not m:
                raise bad_request("bad_filter", f"Can't read filter {name!r}. Use filter[question][op]=value.")
            filters.append((m.group(1), m.group(2) or "eq", value))
    return filters


@router.get(
    "/records",
    summary="Search records across forms",
    description=(
        "Across all forms; for one form, GET /data/{form_key} does the same. Narrow to one form with `form=<key or id>`. `q` matches words in the answers. With a form, "
        "filter answers as `filter[<question>][<op>]=<value>` (ops: eq, ne, in, gt, gte, lt, lte, contains, "
        "exists) and sort by a question key. `complete=false` lists records with required answers missing. "
        "Other sorts: created_at, updated_at, revision (prefix - for newest first)."
    ),
)
def search_records(
    request: Request,
    form: str | None = None,
    q: str | None = None,
    status: str = Query("active", description="active, archived or all"),
    complete: bool | None = None,
    sort: str = "-updated_at",
    page: int = 1,
    page_size: int = Query(25, le=100),
    db: Session = Depends(get_db),
    _: Principal = Depends(read),
):
    f = form_service.get_form(db, form) if form else None
    return record_service.search(db, form=f, q=q, status=status, complete=complete, filters=_filters(request),
                                 sort=sort, page=page, page_size=page_size)


@router.post(
    "/records",
    status_code=201,
    summary="Save a new record",
    description=(
        "Saved against the form's latest published version. Bad answers are refused with 422; missing "
        "required answers are allowed and listed under `missing`. Calculated answers are filled in."
    ),
)
def create_record(body: RecordCreate, response: Response, db: Session = Depends(get_db), p: Principal = Depends(write)):
    form = form_service.get_form(db, body.form)
    rec = record_service.create_record(db, p.user, form, body.answers)
    db.commit()
    response.headers["Location"] = f"/api/v1/records/{rec.id}"
    return _body(db, rec, response)


@router.get("/records/{record_id}", description="Same as GET /data/{form_key}/{record_id}, without the form in the path.", summary="A record with its answers and which questions are shown")
def get_record(record_id: uuid.UUID, response: Response, db: Session = Depends(get_db), _: Principal = Depends(read)):
    return _body(db, record_service.get_record(db, record_id), response)


@router.patch(
    "/records/{record_id}",
    summary="Change answers, or move the record to the latest form version",
    description=(
        "Send only the answers that change; null clears one. Send `form_version: \"latest\"` to move the "
        "record to the form's latest version: it is checked again, and answers to questions the new "
        "version doesn't have are dropped (listed under `dropped`). Send If-Match with the revision you edited."
    ),
)
def update_record(record_id: uuid.UUID, body: RecordPatch, response: Response, if_match: str | None = Header(None),
                  db: Session = Depends(get_db), p: Principal = Depends(write)):
    rec = record_service.get_record(db, record_id)
    rec, dropped = record_service.update_record(db, p.user, rec, body.answers, _base(if_match, body.base_revision),
                                                to_latest=body.form_version == "latest")
    db.commit()
    return _body(db, rec, response, dropped=dropped)


@router.post("/records/{record_id}/archive", summary="Archive a record (it can be restored)")
def archive_record(record_id: uuid.UUID, body: RecordStatusChange, response: Response, if_match: str | None = Header(None),
                   db: Session = Depends(get_db), p: Principal = Depends(write)):
    rec = record_service.set_status(db, p.user, record_service.get_record(db, record_id), "archived", _base(if_match, body.base_revision))
    db.commit()
    return _body(db, rec, response)


@router.post("/records/{record_id}/restore", summary="Restore an archived record")
def restore_record(record_id: uuid.UUID, body: RecordStatusChange, response: Response, if_match: str | None = Header(None),
                   db: Session = Depends(get_db), p: Principal = Depends(write)):
    rec = record_service.set_status(db, p.user, record_service.get_record(db, record_id), "active", _base(if_match, body.base_revision))
    db.commit()
    return _body(db, rec, response)


@router.get("/records/{record_id}/revisions", summary="Every saved revision of a record, newest first")
def record_revisions(record_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(read)):
    return record_service.revisions(db, record_service.get_record(db, record_id))


# --- The same, scoped to one form: /api/v1/data/{form key} ------------------------------

FormKey = Annotated[str, Path(description="The form's key, e.g. person_details (its id works too).")]
SAVE_RULES = (
    "Each save is checked against the record's form version: types and rules, Required if, and calculated "
    "answers, which are worked out here (anything sent for them is ignored). A bad answer is refused with 422 "
    "and every problem listed. Missing required answers are allowed: they're listed under `missing` and the "
    "record has `complete: false` until they're filled. Answers to questions that are hidden are dropped."
)


def _in_form(db: Session, form_key: str, record_id: uuid.UUID):
    form = form_service.get_form(db, form_key)
    rec = record_service.get_record(db, record_id)
    if rec.form_id != form.id:
        raise not_found("Record")
    return rec


@data_router.get(
    "/data/{form_key}",
    summary="Search a form's records",
    description=(
        "`q` matches words anywhere in the answers. Filter answers with `filter[<question>][<op>]=<value>` "
        "(or `filter[<question>]=<value>` for eq). Ops: eq, ne, in (comma-separated), gt, gte, lt, lte, "
        "contains (text), exists. `complete=false` lists records with required answers missing. Sort with "
        "`sort=<question key>`, created_at, updated_at or revision; prefix - for descending. Needs `records:read`."
    ),
)
def search_form_records(
    form_key: FormKey,
    request: Request,
    q: str | None = None,
    status: str = Query("active", description="active, archived or all"),
    complete: bool | None = None,
    sort: str = "-updated_at",
    page: int = 1,
    page_size: int = Query(25, le=100),
    db: Session = Depends(get_db),
    _: Principal = Depends(read),
):
    return record_service.search(db, form=form_service.get_form(db, form_key), q=q, status=status, complete=complete,
                                 filters=_filters(request), sort=sort, page=page, page_size=page_size)


@data_router.post(
    "/data/{form_key}",
    status_code=201,
    summary="Save a new record",
    description="Saved against the form's latest published version. " + SAVE_RULES + " Needs `records:write`.",
)
def create_form_record(form_key: FormKey, body: RecordAnswers, response: Response, db: Session = Depends(get_db),
                       p: Principal = Depends(write)):
    out = create_record(RecordCreate(form=form_key, answers=body.answers), response, db, p)
    response.headers["Location"] = f"/api/v1/data/{out['form']['key']}/{out['id']}"
    return out


@data_router.get(
    "/data/{form_key}/{record_id}",
    summary="Get a record",
    description=(
        "The record's answers (calculated ones included), what's still `missing`, and `fields`: which questions "
        "are visible and required for these answers. The ETag header is the revision; send it back as If-Match "
        "when you change the record. 404 if the record belongs to another form. Needs `records:read`."
    ),
)
def get_form_record(form_key: FormKey, record_id: uuid.UUID, response: Response, db: Session = Depends(get_db),
                    _: Principal = Depends(read)):
    return _body(db, _in_form(db, form_key, record_id), response)


@data_router.patch(
    "/data/{form_key}/{record_id}",
    summary="Change a record",
    description=(
        "Send only the answers that change; null clears one. Send `form_version: \"latest\"` to move the "
        "record to the form's latest version; answers to questions that version doesn't have are dropped and "
        "listed under `dropped`. " + SAVE_RULES + " A stale If-Match is refused with 409. Archived records "
        "can't be changed. Needs `records:write`."
    ),
)
def update_form_record(form_key: FormKey, record_id: uuid.UUID, body: RecordPatch, response: Response,
                       if_match: str | None = Header(None), db: Session = Depends(get_db), p: Principal = Depends(write)):
    _in_form(db, form_key, record_id)
    return update_record(record_id, body, response, if_match, db, p)


@data_router.post("/data/{form_key}/{record_id}/archive", summary="Archive a record",
             description="Hides it from searches (unless status=archived or all) and stops edits. It can be restored. Needs `records:write`.")
def archive_form_record(form_key: FormKey, record_id: uuid.UUID, body: RecordStatusChange, response: Response,
                        if_match: str | None = Header(None), db: Session = Depends(get_db), p: Principal = Depends(write)):
    _in_form(db, form_key, record_id)
    return archive_record(record_id, body, response, if_match, db, p)


@data_router.post("/data/{form_key}/{record_id}/restore", summary="Restore an archived record",
             description="Needs `records:write`.")
def restore_form_record(form_key: FormKey, record_id: uuid.UUID, body: RecordStatusChange, response: Response,
                        if_match: str | None = Header(None), db: Session = Depends(get_db), p: Principal = Depends(write)):
    _in_form(db, form_key, record_id)
    return restore_record(record_id, body, response, if_match, db, p)


@data_router.get("/data/{form_key}/{record_id}/revisions", summary="A record's history",
            description="Every saved revision, newest first: answers, form version, complete, a summary of what changed, who and when. Needs `records:read`.")
def form_record_revisions(form_key: FormKey, record_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(read)):
    return record_service.revisions(db, _in_form(db, form_key, record_id))
