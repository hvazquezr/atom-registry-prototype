"""Internal API used by the web app: schema admin, reviews, users and tokens."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import enrich_change_request
from app.auth import (
    ROLE_SCOPES,
    ROLES,
    SCOPES,
    Principal,
    current_principal,
    hash_password,
    issue_token,
    login,
    require,
    role_scopes,
)
from app.db import SessionLocal, get_db
from app.errors import AppError, ValidationFailed, bad_request, conflict, field_error, not_found
from app.models import ApiToken, SchemaVersion, User
from app.schemas import (
    ExpressionCheck,
    FieldCreate,
    FieldUpdate,
    Login,
    Publish,
    RelationTypeCreate,
    RelationTypeUpdate,
    Reorder,
    Review,
    TokenCreate,
    UserCreate,
    UserUpdate,
)
from app.services import atoms as atom_service
from app.services import change_requests as cr_service
from app.services import expressions
from app.services import fields as field_service
from app.services import relations as rel_service

router = APIRouter(prefix="/internal", tags=["internal"])
schema_admin = require("schema:admin")
approver = require("atoms:approve")
users_admin = require("users:admin")


# --- Auth -------------------------------------------------------------------------------


def _me(p_user: User, scopes: set[str]) -> dict:
    return {
        "id": str(p_user.id), "username": p_user.username, "display_name": p_user.display_name or p_user.username,
        "roles": p_user.roles, "scopes": sorted(scopes),
    }


@router.post("/auth/login")
def do_login(body: Login, db: Session = Depends(get_db)):
    token, raw, user = login(db, body.username, body.password)
    db.commit()
    return {"token": raw, "expires_at": token.expires_at.isoformat(), "user": _me(user, set(token.scopes) & role_scopes(user.roles))}


@router.post("/auth/logout", status_code=204)
def do_logout(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    token = db.get(ApiToken, p.token.id)
    token.revoked_at = dt.datetime.now(dt.UTC)
    db.commit()
    return Response(status_code=204)


@router.get("/auth/me")
def me(p: Principal = Depends(current_principal)):
    return _me(p.user, p.scopes)


# --- Field definitions ------------------------------------------------------------------


@router.get("/fields")
def list_fields(db: Session = Depends(get_db), _: Principal = Depends(require("atoms:read"))):
    return [field_service.field_to_dict(f) for f in field_service.list_fields(db)]


@router.post("/fields", status_code=201)
def create_field(body: FieldCreate, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    row = field_service.save_field(db, body.model_dump())
    db.commit()
    return field_service.field_to_dict(row)


@router.patch("/fields/{field_id}")
def update_field(field_id: uuid.UUID, body: FieldUpdate, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    row = field_service.save_field(db, body.model_dump(exclude_unset=True), field_id)
    db.commit()
    return field_service.field_to_dict(row)


@router.delete("/fields/{field_id}", status_code=204)
def delete_field(field_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    field_service.delete_field(db, field_id)
    db.commit()
    return Response(status_code=204)


@router.post("/fields/{field_id}/deprecate")
def deprecate_field(field_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    row = field_service.set_field_status(db, field_id, "deprecated")
    db.commit()
    return field_service.field_to_dict(row)


@router.post("/fields/{field_id}/restore")
def restore_field(field_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    row = field_service.set_field_status(db, field_id, "active")
    db.commit()
    return field_service.field_to_dict(row)


@router.post("/fields/reorder")
def reorder_fields(body: Reorder, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    rows = field_service.reorder(db, body.keys)
    db.commit()
    return [field_service.field_to_dict(f) for f in rows]


@router.post("/fields/check-expression", summary="Check a calculated-field expression and preview its result")
def check_expression(body: ExpressionCheck, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    others = [field_service.field_to_dict(f) for f in field_service.list_fields(db, include_deprecated=False)]
    candidate = {"key": body.key, "label": "This field", "type": body.type, "config": body.config,
                 "calculated": True, "expression": body.expression}
    try:
        info = field_service.check_expression(candidate, others)
        result = {"ok": True, **info}
        if body.values is not None:
            result["result"] = field_service.evaluate_with_values(candidate, others, body.values)
        return result
    except expressions.ExpressionError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}


# --- Schema publishing ------------------------------------------------------------------


@router.get("/schema/draft")
def draft(db: Session = Depends(get_db), _: Principal = Depends(require("atoms:read"))):
    fields = [field_service.field_to_dict(f) for f in field_service.list_fields(db)]
    return {"fields": fields, "changes": field_service.draft_changes(db), "problems": field_service.check_draft(fields)}


@router.get("/schema/versions")
def versions(db: Session = Depends(get_db), _: Principal = Depends(require("atoms:read"))):
    rows = db.scalars(select(SchemaVersion).order_by(SchemaVersion.version.desc())).all()
    names = cr_service.user_names(db, [r.published_by for r in rows])
    return [
        {"version": r.version, "notes": r.notes, "published_at": r.published_at.isoformat(),
         "published_by_name": names.get(r.published_by), "field_count": len(r.fields)}
        for r in rows
    ]


def _recalculate_in_background(version: int, user_id) -> None:
    with SessionLocal() as db:
        schema = db.get(SchemaVersion, version)
        atom_service.recalculate_all(db, schema, user_id)
        db.commit()


@router.post("/schema/publish", status_code=201)
def publish(body: Publish, background: BackgroundTasks, db: Session = Depends(get_db), p: Principal = Depends(schema_admin)):
    sv, recalc = field_service.publish(db, p.user, body.notes)
    db.commit()
    background.add_task(_recalculate_in_background, sv.version, p.id)
    return {"version": sv.version, "recalculating": recalc, "published_at": sv.published_at.isoformat()}


# --- Relation types ---------------------------------------------------------------------


@router.post("/relation-types", status_code=201)
def create_relation_type(body: RelationTypeCreate, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    rt = rel_service.save_relation_type(db, body.model_dump())
    db.commit()
    return rel_service.relation_type_to_dict(rt)


@router.patch("/relation-types/{rt_id}")
def update_relation_type(rt_id: uuid.UUID, body: RelationTypeUpdate, db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    rt = rel_service.save_relation_type(db, body.model_dump(exclude_unset=True), rt_id)
    db.commit()
    return rel_service.relation_type_to_dict(rt)


# --- Reviews ----------------------------------------------------------------------------


@router.get("/review-queue")
def review_queue(db: Session = Depends(get_db), p: Principal = Depends(approver)):
    rows = cr_service.list_requests(db, status="pending")
    out = [enrich_change_request(db, cr) for cr in rows]
    for item in out:
        item["can_review"] = item["submitted_by"] != str(p.id) or cr_service.get_settings().allow_self_approval
    return out


@router.post("/change-requests/{cr_id}/approve")
def approve(cr_id: uuid.UUID, body: Review, db: Session = Depends(get_db), p: Principal = Depends(approver)):
    cr, atom = cr_service.approve(db, p.user, cr_service.get(db, cr_id), body.comment)
    db.commit()
    return {"change_request": enrich_change_request(db, cr), "atom": atom_service.serialize(atom, atom_service.SchemaCache(db))}


@router.post("/change-requests/{cr_id}/reject")
def reject(cr_id: uuid.UUID, body: Review, db: Session = Depends(get_db), p: Principal = Depends(approver)):
    cr = cr_service.reject(db, p.user, cr_service.get(db, cr_id), body.comment)
    db.commit()
    return enrich_change_request(db, cr)


# --- Jobs -------------------------------------------------------------------------------


@router.post("/jobs/refresh-time-dependent", summary="Refresh stored values of fields that use today()/now()")
def refresh_time_dependent(db: Session = Depends(get_db), _: Principal = Depends(schema_admin)):
    count = atom_service.refresh_time_dependent(db)
    db.commit()
    return {"updated_atoms": count}


# --- Users and tokens -------------------------------------------------------------------


def _user_dict(u: User) -> dict:
    return {"id": str(u.id), "username": u.username, "display_name": u.display_name, "roles": u.roles,
            "is_service_account": u.is_service_account, "is_active": u.is_active}


def _check_roles(roles: list[str]) -> None:
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise ValidationFailed([field_error("roles", "invalid", f"Unknown roles: {', '.join(bad)}. Use {', '.join(ROLES)}.")])


@router.get("/meta/scopes")
def scopes(_: Principal = Depends(current_principal)):
    return {"scopes": SCOPES, "roles": {r: sorted(s) for r, s in ROLE_SCOPES.items()}}


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: Principal = Depends(users_admin)):
    return [_user_dict(u) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), _: Principal = Depends(users_admin)):
    _check_roles(body.roles)
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise conflict("duplicate_user", f"User {body.username!r} already exists.")
    if not body.password and not body.is_service_account:
        raise bad_request("password_required", "People need a password; service accounts don't.")
    u = User(username=body.username, display_name=body.display_name, roles=body.roles,
             is_service_account=body.is_service_account, password_hash=hash_password(body.password) if body.password else None)
    db.add(u)
    db.commit()
    return _user_dict(u)


@router.patch("/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UserUpdate, db: Session = Depends(get_db), p: Principal = Depends(users_admin)):
    u = db.get(User, user_id)
    if not u:
        raise not_found("User")
    if body.roles is not None:
        _check_roles(body.roles)
        if u.id == p.id and "admin" not in body.roles:
            raise conflict("self_lockout", "You can't remove your own admin role.")
        u.roles = body.roles
    if body.display_name is not None:
        u.display_name = body.display_name
    if body.password:
        u.password_hash = hash_password(body.password)
    if body.is_active is not None:
        if u.id == p.id and not body.is_active:
            raise conflict("self_lockout", "You can't disable yourself.")
        u.is_active = body.is_active
    db.commit()
    return _user_dict(u)


def _token_dict(t: ApiToken, username: str | None = None) -> dict:
    return {"id": str(t.id), "name": t.name, "kind": t.kind, "prefix": t.prefix, "scopes": t.scopes,
            "user_id": str(t.user_id), "username": username,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
            "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None}


@router.get("/tokens", summary="Your API tokens (admins see everyone's)")
def list_tokens(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    q = select(ApiToken, User.username).join(User).where(ApiToken.kind == "api").order_by(ApiToken.created_at.desc())
    if "users:admin" not in p.scopes:
        q = q.where(ApiToken.user_id == p.id)
    return [_token_dict(t, name) for t, name in db.execute(q).all()]


@router.post("/tokens", status_code=201, summary="Create an API token. The token is shown only once.")
def create_token(body: TokenCreate, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    owner = p.user
    if body.user_id and body.user_id != p.id:
        if "users:admin" not in p.scopes:
            raise AppError(403, "missing_scope", "Only admins can create tokens for other users.")
        owner = db.get(User, body.user_id)
        if not owner:
            raise not_found("User")
    token, raw = issue_token(db, owner, body.name, body.scopes, expires_at=body.expires_at)
    db.commit()
    return {**_token_dict(token, owner.username), "token": raw}


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    t = db.get(ApiToken, token_id)
    if not t or (t.user_id != p.id and "users:admin" not in p.scopes):
        raise not_found("Token")
    t.revoked_at = dt.datetime.now(dt.UTC)
    db.commit()
    return Response(status_code=204)
