"""Command line tasks.

  python -m app.cli seed            built-in fields, relation types, users; publishes schema v1
  python -m app.cli seed --demo     also adds example fields and atoms
  python -m app.cli refresh-time-dependent   run daily (cron) to refresh today()/now() fields
"""

from __future__ import annotations

import argparse
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.db import SessionLocal
from app.models import FieldDefinition, RelationType, User
from app.services import atoms as atom_service
from app.services import change_requests as cr_service
from app.services import fields as field_service
from app.services import relations as rel_service

BUILT_IN_FIELDS = [
    {"key": "name", "label": "Name", "type": "text", "required": True, "config": {"max_length": 200},
     "description": "Short, unique-ish name of the atom."},
    {"key": "description", "label": "Description", "type": "text", "required": False, "config": {"max_length": 5000},
     "description": "What the atom represents and how it is used."},
]

RELATION_TYPES = [
    {"key": "dependency", "label": "Depends on", "inverse_label": "Dependency of",
     "description": "This atom needs the other atom.", "directed": True, "cardinality": "many_to_many",
     "allow_cycles": False, "require_note": True},
    {"key": "includes", "label": "Includes", "inverse_label": "Included in",
     "description": "This atom contains the other atom.", "directed": True, "cardinality": "many_to_many",
     "allow_cycles": False, "require_note": True},
    {"key": "required_if", "label": "Required if", "inverse_label": "Makes required",
     "description": "This atom becomes required when the other atom is present or answered.", "directed": True,
     "cardinality": "many_to_many", "allow_cycles": True, "require_note": True},
]

DEMO_FIELDS = [
    {"key": "data_type", "label": "Data type", "type": "options", "required": True,
     "config": {"choices": [{"value": "string", "label": "String"}, {"value": "integer", "label": "Integer"},
                            {"value": "decimal", "label": "Decimal"}, {"value": "date", "label": "Date"},
                            {"value": "boolean", "label": "Yes/No"}]}},
    {"key": "tags", "label": "Tags", "type": "options",
     "config": {"multiple": True, "choices": [{"value": "pii", "label": "Personal data"},
                                              {"value": "finance", "label": "Finance"},
                                              {"value": "contact", "label": "Contact"}]}},
    {"key": "code", "label": "Code", "type": "text", "required": True,
     "config": {"regex": "[A-Z]{3}-[0-9]{3}", "regex_message": "must look like ABC-123"}},
    {"key": "max_length", "label": "Maximum length", "type": "number", "config": {"min": 1, "max": 10000, "integer_only": True}},
    {"key": "introduced_on", "label": "Introduced on", "type": "date", "config": {"min": "2000-01-01"}},
    {"key": "synonyms", "label": "Synonyms", "type": "list", "config": {"item_type": "text", "max_items": 10}},
    {"key": "age_days", "label": "Age (days)", "type": "number", "calculated": True,
     "expression": "days_between(introduced_on, today())"},
    {"key": "display_label", "label": "Display label", "type": "text", "calculated": True,
     "expression": 'code + " " + name'},
]

DEMO_ATOMS = [
    {"name": "First name", "code": "PER-001", "data_type": "string", "max_length": 100, "tags": ["pii", "contact"],
     "introduced_on": "2024-03-01", "description": "Given name of a person.", "synonyms": ["Given name", "Forename"]},
    {"name": "Last name", "code": "PER-002", "data_type": "string", "max_length": 100, "tags": ["pii", "contact"],
     "introduced_on": "2024-03-01", "description": "Family name of a person.", "synonyms": ["Surname"]},
    {"name": "Full name", "code": "PER-003", "data_type": "string", "max_length": 201, "tags": ["pii"],
     "introduced_on": "2024-06-15", "description": "First and last name together."},
    {"name": "Has middle name", "code": "PER-004", "data_type": "boolean", "introduced_on": "2025-01-10"},
    {"name": "Middle name", "code": "PER-005", "data_type": "string", "max_length": 100, "tags": ["pii"],
     "introduced_on": "2025-01-10"},
    {"name": "Annual income", "code": "FIN-001", "data_type": "decimal", "tags": ["finance", "pii"],
     "introduced_on": "2025-09-01", "description": "Gross yearly income in USD."},
]
DEMO_RELATIONS = [
    ("Full name", "dependency", "First name", "Built from the first name."),
    ("Full name", "dependency", "Last name", "Built from the last name."),
    ("Full name", "includes", "Middle name", "Shown between first and last name when present."),
    ("Middle name", "required_if", "Has middle name", "Ask only when the person says they have one."),
]


def _user(db: Session, username: str, display: str, roles: list[str], password: str) -> User:
    u = db.scalars(select(User).where(User.username == username)).first()
    if not u:
        u = User(username=username, display_name=display, roles=roles, password_hash=hash_password(password))
        db.add(u)
        db.flush()
    return u


def seed(db: Session, demo: bool = False, password: str | None = None) -> dict:
    password = password or os.environ.get("ATOM_SEED_PASSWORD", "changeme")
    admin = _user(db, "admin", "Admin", ["admin"], password)
    editor = _user(db, "editor", "Edith Editor", ["editor"], password)
    reviewer = _user(db, "reviewer", "Rey Reviewer", ["reviewer", "editor"], password)

    existing = {f.key for f in db.scalars(select(FieldDefinition))}
    for i, f in enumerate(BUILT_IN_FIELDS):
        if f["key"] not in existing:
            row = field_service.save_field(db, dict(f, position=i))
            row.system = True
    existing_rt = {r.key for r in db.scalars(select(RelationType))}
    for rt in RELATION_TYPES:
        if rt["key"] not in existing_rt:
            rel_service.save_relation_type(db, rt)
    if demo:
        for i, f in enumerate(DEMO_FIELDS):
            if f["key"] not in existing:
                field_service.save_field(db, dict(f, position=len(BUILT_IN_FIELDS) + i))
    db.flush()
    if field_service.draft_changes(db)["has_changes"] or field_service.latest_schema(db) is None:
        field_service.publish(db, admin, "Initial schema" if not demo else "Initial schema with demo fields")

    if demo and not atom_service.search(db, page_size=1)["total"]:
        ids = {}
        for values in DEMO_ATOMS:
            cr = cr_service.submit(db, editor, "create", values=values, comment="Demo data")
            _, atom = cr_service.approve(db, reviewer, cr)
            ids[values["name"]] = atom.id
        for src, rtype, tgt, note in DEMO_RELATIONS:
            cr = cr_service.submit(db, editor, "update", atom_id=ids[src],
                                   relations={"add": [{"type": rtype, "target_atom_id": str(ids[tgt]), "note": note}]})
            cr_service.approve(db, reviewer, cr)
        # one pending request so the review queue isn't empty
        cr_service.submit(db, editor, "update", atom_id=ids["Annual income"],
                          values={"max_length": 12, "description": "Gross yearly income in USD, before tax."},
                          comment="Clarify that income is before tax.")
    db.commit()
    return {"admin": admin.username, "editor": editor.username, "reviewer": reviewer.username}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--demo", action="store_true")
    sub.add_parser("refresh-time-dependent")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.command == "seed":
            users = seed(db, demo=args.demo)
            print(f"Seeded. Users: {', '.join(users.values())} (password from ATOM_SEED_PASSWORD, default 'changeme').")
        else:
            n = atom_service.refresh_time_dependent(db)
            db.commit()
            print(f"Refreshed {n} atoms.")


if __name__ == "__main__":
    main()
