"""Request bodies. Responses are plain JSON built by the services."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

FieldType = Literal["text", "number", "date", "list", "options"]


class FieldCreate(BaseModel):
    key: str = Field(..., examples=["unit_price"])
    label: str
    description: str = ""
    type: FieldType
    required: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    calculated: bool = False
    expression: str | None = Field(None, examples=["unit_price * quantity"])
    position: int | None = None


class FieldUpdate(BaseModel):
    key: str | None = None
    label: str | None = None
    description: str | None = None
    type: FieldType | None = None
    required: bool | None = None
    config: dict[str, Any] | None = None
    calculated: bool | None = None
    expression: str | None = None
    position: int | None = None


class ExpressionCheck(BaseModel):
    expression: str
    type: FieldType = "text"
    config: dict[str, Any] = Field(default_factory=dict)
    key: str = Field("_preview", description="Key of the field being edited, so it is excluded from its own inputs.")
    values: dict[str, Any] | None = Field(None, description="Optional values to test the expression with.")


class Reorder(BaseModel):
    keys: list[str]


class Publish(BaseModel):
    notes: str = ""


class RelationTypeCreate(BaseModel):
    key: str = Field(..., examples=["derived_from"])
    label: str
    inverse_label: str = ""
    description: str = ""
    directed: bool = True
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_many"
    allow_cycles: bool = True
    require_note: bool = True
    attributes: list[dict[str, Any]] = Field(default_factory=list)


class RelationTypeUpdate(BaseModel):
    label: str | None = None
    inverse_label: str | None = None
    description: str | None = None
    directed: bool | None = None
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] | None = None
    allow_cycles: bool | None = None
    require_note: bool | None = None
    attributes: list[dict[str, Any]] | None = None
    status: Literal["active", "deprecated"] | None = None


class RelationAdd(BaseModel):
    type: str
    target_atom_id: uuid.UUID
    direction: Literal["out", "in"] = "out"
    note: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class RelationChanges(BaseModel):
    add: list[RelationAdd] = Field(default_factory=list)
    remove: list[uuid.UUID] = Field(default_factory=list)


class AtomCreate(BaseModel):
    values: dict[str, Any]
    relations: list[RelationAdd] = Field(default_factory=list)
    comment: str = ""


class AtomPreview(BaseModel):
    values: dict[str, Any]
    atom_id: uuid.UUID | None = Field(None, description="Preview a patch on this atom instead of a new atom.")


class AtomPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict, description="Only the fields to change. null clears a value.")
    relations: RelationChanges = Field(default_factory=RelationChanges)
    base_revision: int | None = Field(None, description="Revision you edited. Also accepted as the If-Match header.")
    comment: str = ""


class StatusChange(BaseModel):
    base_revision: int | None = None
    comment: str = ""


class ChangeRequestCreate(BaseModel):
    kind: Literal["create", "update", "deprecate", "restore"]
    atom_id: uuid.UUID | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    relations: RelationChanges = Field(default_factory=RelationChanges)
    base_revision: int | None = None
    comment: str = ""


class ChangeRequestRevise(BaseModel):
    values: dict[str, Any] | None = None
    relations: RelationChanges | None = None
    comment: str | None = None


class Review(BaseModel):
    comment: str = ""


class Login(BaseModel):
    username: str
    password: str


class TokenCreate(BaseModel):
    name: str
    scopes: list[str] | None = Field(None, description="Defaults to everything your roles allow.")
    expires_at: dt.datetime | None = None
    user_id: uuid.UUID | None = Field(None, description="Admins can create tokens for service accounts.")


class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    password: str | None = Field(None, description="Leave empty for service accounts that only use API tokens.")
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    is_service_account: bool = False


class UserUpdate(BaseModel):
    display_name: str | None = None
    password: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None


# --- Forms ------------------------------------------------------------------------------


class FormItemIn(BaseModel):
    atom_id: uuid.UUID
    key: str | None = Field(None, description="Answer key, unique in the form. Defaults to one made from the atom's name.")
    label: str = Field("", description="Overrides the atom's name on this form.")
    help_text: str = Field("", description="Overrides the atom's description on this form.")
    required: bool = Field(False, description="Always required on this form. Can't be combined with Required if.")


class FormSectionIn(BaseModel):
    title: str = ""
    description: str = ""
    items: list[FormItemIn] = Field(default_factory=list)


class FormCreate(BaseModel):
    key: str = Field(..., examples=["new_customer"])
    name: str
    description: str = ""
    sections: list[FormSectionIn] | None = None


class FormDraft(BaseModel):
    name: str | None = None
    description: str | None = None
    sections: list[FormSectionIn] | None = Field(None, description="Replaces every section and item of the draft.")
    base_revision: int | None = Field(None, description="Draft revision you edited. Also accepted as the If-Match header.")


class FormPublish(BaseModel):
    notes: str = Field("", description="What changed, shown in the version history.")
    comment: str = ""


class FormStatusChange(BaseModel):
    comment: str = ""


class FormAnswers(BaseModel):
    version: int | Literal["latest", "draft"] = Field("latest", description="Published version number, latest, or draft.")
    answers: dict[str, Any] = Field(default_factory=dict, description="Answers keyed by item key.")


class FormInputSettings(BaseModel):
    type_field: str | None = None
    type_map: dict[str, str] | None = None
    default_type: str | None = None
    min_length_field: str | None = None
    max_length_field: str | None = None
    min_field: str | None = None
    max_field: str | None = None
    regex_field: str | None = None
    choices_field: str | None = None
    formula_field: str | None = None
