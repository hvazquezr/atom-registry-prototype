"""Database tables.

Field definitions and relation types make up the "meta atom". Field definitions are a working
draft; publishing snapshots them into schema_version, and atoms are validated against the
latest published version. Atom values live in one JSONB column keyed by field key. Links
between atoms are rows in atom_relation. Every change to an atom goes through change_request.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --- Users and tokens -------------------------------------------------------------------


class User(TimestampMixin, Base):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str | None] = mapped_column(String(300))
    # Roles: viewer, editor, reviewer, admin. Scopes are derived from roles (see auth.py).
    roles: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_service_account: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ApiToken(Base):
    __tablename__ = "api_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="api")  # api | session
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


# --- Meta atom --------------------------------------------------------------------------


class FieldDefinition(TimestampMixin, Base):
    __tablename__ = "field_definition"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # text|number|date|list|options
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    calculated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expression: Mapped[str | None] = mapped_column(Text)
    depends_on: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    time_dependent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set once the field has appeared in a published schema; its key is frozen after that.
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    fields: Mapped[list] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RelationType(TimestampMixin, Base):
    __tablename__ = "relation_type"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    inverse_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    directed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # one_to_one | one_to_many | many_to_one | many_to_many (read as source : target)
    cardinality: Mapped[str] = mapped_column(String(20), nullable=False, default="many_to_many")
    allow_cycles: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Whether every link of this type must carry a note explaining it.
    require_note: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Field-like definitions for attributes carried by the link itself.
    attributes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


# --- Atoms ------------------------------------------------------------------------------


class Atom(TimestampMixin, Base):
    __tablename__ = "atom"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    schema_version: Mapped[int] = mapped_column(ForeignKey("schema_version.version"))
    values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))

    __table_args__ = (
        Index("ix_atom_values", "values", postgresql_using="gin", postgresql_ops={"values": "jsonb_path_ops"}),
        Index("ix_atom_search", "search_vector", postgresql_using="gin"),
        Index("ix_atom_status", "status"),
    )


class AtomRevision(Base):
    __tablename__ = "atom_revision"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    atom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("atom.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    relations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("change_request.id"))
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("atom_id", "revision"),)


class AtomRelation(Base):
    __tablename__ = "atom_relation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    relation_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relation_type.id"))
    source_atom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("atom.id", ondelete="CASCADE"))
    target_atom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("atom.id", ondelete="CASCADE"))
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    relation_type: Mapped[RelationType] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("relation_type_id", "source_atom_id", "target_atom_id"),
        Index("ix_relation_source", "source_atom_id", "relation_type_id"),
        Index("ix_relation_target", "target_atom_id", "relation_type_id"),
    )


class ChangeRequest(Base):
    __tablename__ = "change_request"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    atom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("atom.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # create|update|deprecate|restore
    proposed_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # {"add": [{"type": key, "target_atom_id": id, "direction": "out"|"in", "note": "", "attributes": {}}],
    #  "remove": [relation_id, ...]}
    proposed_relations: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    base_revision: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
