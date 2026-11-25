"""Records: saved answers to a published form.

Forms are the record types. A record points to the form version it was saved against and holds
one cleaned answer per question key. Every save runs the same check as
POST /forms/{id}/validate (types and rules, Required if, calculated answers):

  * A bad answer (wrong type, out of range, failed calculation, unknown question) blocks the save.
  * Missing required answers don't. They're listed in `missing`, and the record is incomplete
    until there are none.
  * Answers to hidden questions are dropped, and calculated answers are always worked out here;
    anything sent for them is ignored.

Records save directly, with no review, and every save writes a record_revision. Records stay on
their form version until someone moves them to the latest one, which checks them again.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

from sqlalchemy import Float, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.errors import ValidationFailed, bad_request, conflict, not_found
from app.models import Form, FormVersion, Record, RecordRevision, User
from app.services import change_requests as cr_service
from app.services import forms as form_service
from app.services.validation import is_empty

FILTER_OPS = ("eq", "ne", "in", "gt", "gte", "lt", "lte", "contains", "exists")
TIME_FUNCTION = re.compile(r"\b(today|now)\s*\(")
SORTS = ("created_at", "updated_at", "revision")


# --- Form definitions ---------------------------------------------------------------------


class Definitions:
    """Caches published form versions while serializing many records."""

    def __init__(self, db: Session):
        self.db = db
        self._versions: dict[tuple[uuid.UUID, int], dict] = {}
        self._forms: dict[uuid.UUID, Form] = {}

    def form(self, form_id: uuid.UUID) -> Form:
        if form_id not in self._forms:
            self._forms[form_id] = self.db.get(Form, form_id)
        return self._forms[form_id]

    def get(self, form_id: uuid.UUID, version: int) -> dict:
        k = (form_id, version)
        if k not in self._versions:
            fv = self.db.scalars(select(FormVersion).where(FormVersion.form_id == form_id, FormVersion.version == version)).first()
            if not fv:
                raise not_found(f"Version {version} of the form")
            self._versions[k] = fv.definition
        return self._versions[k]


def _items(definition: dict) -> list[dict]:
    return [i for s in definition["sections"] for i in s["items"]]


def _calculated_keys(definition: dict) -> set[str]:
    return {i["key"] for i in _items(definition) if i.get("calculation")}


def time_dependent(definition: dict) -> bool:
    return any(TIME_FUNCTION.search(i["calculation"]["formula"]) for i in _items(definition) if i.get("calculation"))


def entered(answers: dict, definition: dict) -> dict:
    """The answers a person gave: everything except calculated ones."""
    calc = _calculated_keys(definition)
    return {k: v for k, v in answers.items() if k not in calc}


def check(definition: dict, answers: dict, now=None) -> tuple[dict, list[str]]:
    """Validate answers for saving. Returns (clean answers with calculated ones, missing keys)."""
    result = form_service.validate_answers(definition, answers, now)
    blocking = [e for e in result["errors"] if e["code"] != "required"]
    if blocking:
        raise ValidationFailed(blocking)
    missing = [e["field"] for e in result["errors"] if e["code"] == "required"]
    return result["answers"], missing


def _search_text(definition: dict, answers: dict) -> str:
    parts = []
    for i in _items(definition):
        v = answers.get(i["key"])
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            parts.append(str(v))
    return " ".join(parts)[:10000]


def title(definition: dict, answers: dict) -> str:
    """A short label for lists: the first two text answers in form order."""
    texts = [answers[i["key"]] for i in _items(definition)
             if isinstance(answers.get(i["key"]), str) and i["input"]["type"] == "text"]
    if texts:
        return " ".join(texts[:2])[:200]
    for i in _items(definition):
        if not is_empty(answers.get(i["key"])):
            return f"{i['label']}: {answers[i['key']]}"[:200]
    return "Empty record"


# --- Reading ------------------------------------------------------------------------------


def get_record(db: Session, record_id: uuid.UUID) -> Record:
    rec = db.get(Record, record_id)
    if not rec:
        raise not_found("Record")
    return rec


def current_answers(rec: Record, definition: dict, now=None) -> tuple[dict, list[str]]:
    """Stored answers, with today()/now() formulas worked out again."""
    if not time_dependent(definition):
        return rec.answers, rec.missing
    result = form_service.validate_answers(definition, entered(rec.answers, definition), now)
    answers = dict(rec.answers)
    for k in _calculated_keys(definition):
        if k in result["answers"]:
            answers[k] = result["answers"][k]
        else:
            answers.pop(k, None)
    return answers, rec.missing


def serialize(rec: Record, defs: Definitions, names: dict | None = None, now=None, detail: bool = False) -> dict:
    form = defs.form(rec.form_id)
    definition = defs.get(rec.form_id, rec.form_version)
    answers, missing = current_answers(rec, definition, now)
    names = names or {}
    out = {
        "id": str(rec.id),
        "form": {"id": str(form.id), "key": form.key, "name": form.name, "status": form.status},
        "form_version": rec.form_version,
        "latest_form_version": form.latest_version,
        "title": title(definition, answers),
        "answers": answers,
        "missing": missing,
        "complete": not missing,
        "status": rec.status,
        "revision": rec.revision,
        "created_at": rec.created_at.isoformat(),
        "updated_at": rec.updated_at.isoformat(),
        "created_by": str(rec.created_by) if rec.created_by else None,
        "created_by_name": names.get(rec.created_by),
        "updated_by": str(rec.updated_by) if rec.updated_by else None,
        "updated_by_name": names.get(rec.updated_by),
    }
    if detail:
        state = form_service.validate_answers(definition, entered(answers, definition), now)
        out["fields"] = state["fields"]
    return out


def serialize_many(db: Session, records: list[Record], detail: bool = False) -> list[dict]:
    defs = Definitions(db)
    names = cr_service.user_names(db, [r.created_by for r in records] + [r.updated_by for r in records])
    now = dt.datetime.now(dt.UTC)
    return [serialize(r, defs, names, now, detail) for r in records]


def revisions(db: Session, rec: Record) -> list[dict]:
    revs = list(db.scalars(select(RecordRevision).where(RecordRevision.record_id == rec.id).order_by(RecordRevision.revision.desc())))
    names = cr_service.user_names(db, [r.changed_by for r in revs])
    return [
        {
            "revision": r.revision, "form_version": r.form_version, "answers": r.answers, "complete": r.complete,
            "status": r.status, "summary": r.summary,
            "changed_by": str(r.changed_by) if r.changed_by else None, "changed_by_name": names.get(r.changed_by),
            "changed_at": r.changed_at.isoformat(),
        }
        for r in revs
    ]


# --- Searching ----------------------------------------------------------------------------


def _filter_clause(item: dict, op: str, value: str):
    key = item["key"]
    spec = item["input"]
    t = spec["type"]
    multiple = t == "options" and (spec.get("config") or {}).get("multiple")
    raw = Record.answers[key].astext
    if op == "exists":
        present = Record.answers.has_key(key)  # noqa: W601 - SQLAlchemy JSONB operator
        return present if value.lower() in ("1", "true", "yes", "") else ~present

    def coerce(v: str):
        if t == "number":
            try:
                return float(v)
            except ValueError as exc:
                raise bad_request("bad_filter", f"{v!r} is not a number for {key}.") from exc
        if t == "boolean":
            if v.lower() not in ("true", "false", "yes", "no"):
                raise bad_request("bad_filter", f"{key} is yes/no; filter it with true or false.")
            return v.lower() in ("true", "yes")
        if t == "date":
            try:
                return dt.date.fromisoformat(v).isoformat()
            except ValueError as exc:
                raise bad_request("bad_filter", f"{v!r} is not a date (YYYY-MM-DD) for {key}.") from exc
        return v

    if multiple:
        if op in ("eq", "contains"):
            return Record.answers.contains(cast({key: [value]}, JSONB))
        if op == "in":
            return or_(*[Record.answers.contains(cast({key: [v]}, JSONB)) for v in value.split(",")])
        raise bad_request("bad_filter", f"{op} can't be used on {key}; use eq, in, contains or exists.")
    if op == "eq":
        return Record.answers.contains(cast({key: coerce(value)}, JSONB))  # uses the GIN index
    if op == "ne":
        return or_(~Record.answers.has_key(key), ~Record.answers.contains(cast({key: coerce(value)}, JSONB)))
    if op == "in":
        return or_(*[Record.answers.contains(cast({key: coerce(v)}, JSONB)) for v in value.split(",")])
    if op == "contains":
        if t != "text":
            raise bad_request("bad_filter", f"contains only works on text answers, not {key}.")
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return raw.ilike(f"%{escaped}%")
    if t == "boolean" or (t == "options"):
        raise bad_request("bad_filter", f"{op} can't be used on {key}; use eq, ne, in or exists.")
    cmp = {"gt": "__gt__", "gte": "__ge__", "lt": "__lt__", "lte": "__le__"}[op]
    expr = cast(raw, Float) if t == "number" else raw
    return getattr(expr, cmp)(coerce(value))


def search(
    db: Session,
    form: Form | None = None,
    q: str | None = None,
    status: str = "active",
    complete: bool | None = None,
    filters: list[tuple[str, str, str]] | None = None,
    sort: str = "-updated_at",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    query = select(Record)
    definition = None
    if form is not None:
        query = query.where(Record.form_id == form.id)
        if form.latest_version:
            definition = form_service.get_version(db, form, "latest").definition
    if status and status != "all":
        if status not in ("active", "archived"):
            raise bad_request("bad_status", "status must be active, archived or all.")
        query = query.where(Record.status == status)
    if complete is not None:
        query = query.where(Record.complete.is_(complete))
    if q:
        for word in q.split()[:10]:
            escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(Record.search_text.ilike(f"%{escaped}%"))
    items = {i["key"]: i for i in _items(definition)} if definition else {}
    for key, op, value in filters or []:
        if form is None:
            raise bad_request("bad_filter", "Answer filters need a form; add form=<key>.")
        if op not in FILTER_OPS:
            raise bad_request("bad_filter", f"Unknown filter operator {op!r}. Use one of {', '.join(FILTER_OPS)}.")
        if key not in items:
            raise bad_request("bad_filter", f"The form has no question {key!r}.")
        query = query.where(_filter_clause(items[key], op, value))

    desc = sort.startswith("-")
    sort_key = sort.lstrip("-")
    if sort_key in SORTS:
        col = getattr(Record, sort_key)
    elif sort_key in items:
        col = cast(Record.answers[sort_key].astext, Float) if items[sort_key]["input"]["type"] == "number" else Record.answers[sort_key].astext
    else:
        raise bad_request("bad_sort", f"Can't sort by {sort_key!r}. Use {', '.join(SORTS)} or a question key (with form=).")
    order = col.desc().nulls_last() if desc else col.asc().nulls_last()

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page, page_size = max(1, page), max(1, min(page_size, 100))
    rows = list(db.scalars(query.order_by(order, Record.id).offset((page - 1) * page_size).limit(page_size)))
    return {"items": serialize_many(db, rows), "total": total, "page": page, "page_size": page_size}


# --- Saving -------------------------------------------------------------------------------


def _write_revision(db: Session, rec: Record, user: User, summary: str) -> None:
    db.add(RecordRevision(
        record_id=rec.id, revision=rec.revision, form_version=rec.form_version, answers=rec.answers,
        complete=rec.complete, status=rec.status, summary=summary, changed_by=user.id,
    ))


def _apply(rec: Record, definition: dict, clean: dict, missing: list[str]) -> None:
    rec.answers = clean
    rec.missing = missing
    rec.complete = not missing
    rec.search_text = _search_text(definition, clean)


def _latest(db: Session, form: Form) -> FormVersion:
    if not form.latest_version:
        raise conflict("form_not_published", f"{form.name} has no published version yet, so it can't hold records.")
    return form_service.get_version(db, form, "latest")


def create_record(db: Session, user: User, form: Form, answers: dict, now=None) -> Record:
    if form.status != "active":
        raise conflict("form_deprecated", f"{form.name} is deprecated, so it can't get new records.")
    fv = _latest(db, form)
    if not isinstance(answers, dict):
        raise bad_request("bad_answers", "answers must be an object keyed by question key.")
    clean, missing = check(fv.definition, answers, now)
    rec = Record(form_id=form.id, form_version=fv.version, revision=1, status="active",
                 created_by=user.id, updated_by=user.id)
    _apply(rec, fv.definition, clean, missing)
    db.add(rec)
    db.flush()
    _write_revision(db, rec, user, "Created")
    return rec


def _changed_labels(definition: dict, old: dict, new: dict) -> list[str]:
    labels = {i["key"]: i["label"] for i in _items(definition)}
    calc = _calculated_keys(definition)
    keys = [k for k in labels if k not in calc and old.get(k) != new.get(k)]
    return [labels[k] for k in keys]


def update_record(db: Session, user: User, rec: Record, answers: dict | None = None,
                  base_revision: int | None = None, to_latest: bool = False, now=None) -> tuple[Record, list[str]]:
    """Change some answers (null clears one) and/or move the record to the form's latest version.
    Returns the record and the answers dropped because the new version has no such question."""
    if rec.status != "active":
        raise conflict("record_archived", "This record is archived. Restore it before editing.")
    if base_revision is not None and base_revision != rec.revision:
        raise conflict("stale_revision", f"The record changed since revision {base_revision}; it is now at {rec.revision}.",
                       {"current_revision": rec.revision})
    if answers is not None and not isinstance(answers, dict):
        raise bad_request("bad_answers", "answers must be an object keyed by question key.")
    form = db.get(Form, rec.form_id)
    old_def = form_service.get_version(db, form, rec.form_version).definition
    version = rec.form_version
    definition = old_def
    if to_latest:
        fv = _latest(db, form)
        version, definition = fv.version, fv.definition

    merged = entered(rec.answers, old_def)
    for k, v in (answers or {}).items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    dropped: list[str] = []
    if version != rec.form_version:
        keys = {i["key"] for i in _items(definition)}
        dropped = sorted(k for k in merged if k not in keys and k not in (answers or {}))
        for k in dropped:
            merged.pop(k)
    clean, missing = check(definition, merged, now)

    if version == rec.form_version and clean == rec.answers and missing == rec.missing:
        return rec, dropped
    parts = []
    if version != rec.form_version:
        parts.append(f"Moved to version {version}")
    changed = _changed_labels(definition, entered(rec.answers, old_def), entered(clean, definition))
    if changed:
        parts.append("Changed " + ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else ""))
    rec.form_version = version
    _apply(rec, definition, clean, missing)
    rec.revision += 1
    rec.updated_by = user.id
    _write_revision(db, rec, user, "; ".join(parts) or "Recalculated")
    return rec, dropped


def set_status(db: Session, user: User, rec: Record, status: str, base_revision: int | None = None) -> Record:
    if base_revision is not None and base_revision != rec.revision:
        raise conflict("stale_revision", f"The record changed since revision {base_revision}; it is now at {rec.revision}.",
                       {"current_revision": rec.revision})
    if rec.status == status:
        raise conflict("no_change", f"The record is already {status}.")
    rec.status = status
    rec.revision += 1
    rec.updated_by = user.id
    _write_revision(db, rec, user, "Archived" if status == "archived" else "Restored")
    return rec

