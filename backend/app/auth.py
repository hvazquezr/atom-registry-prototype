"""Bearer-token authentication and scopes.

For the prototype the registry issues its own tokens:
  * session tokens from POST /internal/auth/login (used by the web app), and
  * API tokens for scripts and other services, created in the app or via the internal API.
Only a SHA-256 hash of each token is stored. Validating tokens from an external identity
provider (OIDC/JWT) can be added later in `authenticate()` without touching the routes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import ApiToken, User

SCOPES = {
    "atoms:read": "Search and read atoms, relations and the schema",
    "atoms:write": "Submit change requests",
    "atoms:approve": "Approve or reject change requests (atoms and forms)",
    "forms:read": "Search and read forms, their versions, and check answers",
    "forms:write": "Create forms, edit drafts and submit them for review",
    "schema:admin": "Edit field definitions and relation types, publish the schema",
    "users:admin": "Manage users and their tokens",
}
ROLE_SCOPES = {
    "viewer": {"atoms:read", "forms:read"},
    "editor": {"atoms:read", "atoms:write", "forms:read", "forms:write"},
    "reviewer": {"atoms:read", "atoms:approve", "forms:read"},
    "admin": set(SCOPES),
}
ROLES = tuple(ROLE_SCOPES)
TOKEN_PREFIX = "atr_"

bearer = HTTPBearer(auto_error=False)


def role_scopes(roles: list[str]) -> set[str]:
    out: set[str] = set()
    for r in roles or []:
        out |= ROLE_SCOPES.get(r, set())
    return out


# --- Passwords --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, rounds, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(candidate.hex(), digest)


# --- Tokens -----------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user: User, name: str, scopes: list[str] | None = None, kind: str = "api",
                expires_at: dt.datetime | None = None) -> tuple[ApiToken, str]:
    allowed = role_scopes(user.roles)
    requested = set(scopes) if scopes else allowed
    unknown = requested - set(SCOPES)
    if unknown:
        raise AppError(400, "bad_scope", f"Unknown scopes: {', '.join(sorted(unknown))}.")
    if not requested <= allowed:
        raise AppError(403, "scope_not_allowed", f"{user.username} can't grant: {', '.join(sorted(requested - allowed))}.")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(user_id=user.id, name=name, kind=kind, prefix=raw[:12], token_hash=_hash_token(raw),
                     scopes=sorted(requested), expires_at=expires_at)
    db.add(token)
    db.flush()
    return token, raw


def login(db: Session, username: str, password: str) -> tuple[ApiToken, str, User]:
    user = db.scalars(select(User).where(User.username == username)).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise AppError(401, "bad_credentials", "Wrong username or password.")
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(hours=get_settings().session_token_hours)
    token, raw = issue_token(db, user, "Web session", kind="session", expires_at=expires)
    return token, raw, user


class Principal:
    def __init__(self, user: User, token: ApiToken):
        self.user = user
        self.token = token
        # A token never has more access than its user's current roles.
        self.scopes = set(token.scopes) & role_scopes(user.roles)

    @property
    def id(self) -> uuid.UUID:
        return self.user.id


def authenticate(db: Session, raw: str) -> Principal:
    token = db.scalars(select(ApiToken).where(ApiToken.token_hash == _hash_token(raw))).first()
    now = dt.datetime.now(dt.UTC)
    if not token or token.revoked_at or (token.expires_at and token.expires_at < now):
        raise AppError(401, "invalid_token", "The token is invalid, expired or revoked.")
    user = db.get(User, token.user_id)
    if not user or not user.is_active:
        raise AppError(401, "invalid_token", "The token's user is disabled.")
    if not token.last_used_at or (now - token.last_used_at).total_seconds() > 60:
        token.last_used_at = now
        db.commit()
    return Principal(user, token)


def current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if not creds or creds.scheme.lower() != "bearer":
        raise AppError(401, "not_authenticated", "Send a token in the Authorization header: Bearer <token>.")
    principal = authenticate(db, creds.credentials)
    request.state.principal = principal
    return principal


def require(*scopes: str) -> Callable[..., Principal]:
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        missing = [s for s in scopes if s not in principal.scopes]
        if missing:
            raise AppError(403, "missing_scope", f"This needs the {', '.join(missing)} scope.")
        return principal

    return dependency
