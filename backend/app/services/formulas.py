"""Answer formulas: atoms whose answer on a form is calculated from other atoms' answers.

An atom's formula lives in an ordinary atom field (`formula` by default; the form-input settings
can name another one). It is a CEL expression in which `@name` stands for another atom's answer,
where `name` is the atom's name in lowercase with underscores ("Date of birth" -> @date_of_birth):

    today().getFullYear() - @date_of_birth.getFullYear()
    @weight / (@height * @height)

When a change to an atom is approved, each @name is bound to its atom through a "Calculated from"
link, which carries the name in its `ref` attribute. Those links are kept in step with the formula
here and can't be edited by hand. Because a formula is bound by link, renaming an input atom later
doesn't break it. Links of this type can't form loops, so calculations always finish.

On a form, an atom with a formula is read-only: its answer is calculated once every input atom is
answered, and publishing is blocked until every input atom is on the form.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError, ValidationFailed, field_error
from app.models import Atom, AtomRelation, RelationType
from app.services import expressions

LINK_TYPE = "calculated_from"
DEFAULT_FORMULA_FIELD = "formula"
_IDENT = re.compile(r"[a-z][a-z0-9_]*")
_SLUG = re.compile(r"[^a-z0-9]+")


class FormulaError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def ref_name(atom_name: str | None) -> str:
    """The name an atom goes by after @ in formulas."""
    s = _SLUG.sub("_", (atom_name or "").lower()).strip("_")
    if s and not s[0].isalpha():
        s = "a_" + s
    return s


def formula_field(db: Session) -> str | None:
    from app.services.forms import input_settings

    return input_settings(db).get("formula_field")


# --- Parsing ----------------------------------------------------------------------------


def translate(source: str) -> tuple[str, list[str]]:
    """Turn @name references into plain CEL identifiers. Returns (cel_source, refs in order).

    Text inside string literals is left alone, so "a@b.com" stays as it is.
    """
    out: list[str] = []
    refs: list[str] = []
    i, n = 0, len(source)
    quote: str | None = None
    while i < n:
        c = source[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "@":
            m = _IDENT.match(source, i + 1)
            if not m:
                raise FormulaError("After @ comes an atom's name in lowercase with underscores, like @date_of_birth.")
            name = m.group(0)
            if name not in refs:
                refs.append(name)
            out.append(name)
            i = m.end()
            continue
        out.append(c)
        i += 1
    return "".join(out), refs


def parse(source: str) -> tuple[expressions.ParsedExpression, list[str]]:
    """Parse a formula. Raises FormulaError with a message for people."""
    cel, refs = translate(source or "")
    if not refs:
        raise FormulaError("A formula needs at least one other atom, written with @, like @date_of_birth.")
    try:
        parsed = expressions.parse(cel)
    except expressions.ExpressionError as exc:
        raise FormulaError(exc.message) from exc
    bare = sorted(parsed.variables - set(refs))
    if bare:
        raise FormulaError("Put @ in front of atom names: " + ", ".join(f"@{b}" for b in bare) + ".")
    return parsed, refs


# --- Binding @names to atoms ------------------------------------------------------------


def link_type(db: Session) -> RelationType | None:
    return db.scalars(select(RelationType).where(RelationType.key == LINK_TYPE)).first()


def links(db: Session, atom_id: uuid.UUID) -> list[AtomRelation]:
    rt = link_type(db)
    if not rt:
        return []
    return list(db.scalars(
        select(AtomRelation).where(AtomRelation.relation_type_id == rt.id, AtomRelation.source_atom_id == atom_id)
        .order_by(AtomRelation.created_at)
    ))


def _candidates(db: Session, ref: str) -> list[Atom]:
    pattern = ref.replace("a_", "", 1) if ref.startswith("a_") else ref
    like = "%".join(pattern.split("_"))
    rows = db.scalars(select(Atom).where(func.lower(Atom.values["name"].astext).ilike(f"%{like}%")))
    return [a for a in rows if ref_name(a.values.get("name")) == ref]


def bind(db: Session, atom_id: uuid.UUID | None, refs: list[str]) -> tuple[dict[str, Atom], list[str]]:
    """Find the atom behind each @name. Returns ({ref: atom}, problems).

    A name already bound by one of this atom's links keeps pointing at that atom, even if the
    atom was renamed since. A new name must match exactly one active atom.
    """
    bound = {}
    if atom_id:
        for rel in links(db, atom_id):
            ref = (rel.attributes or {}).get("ref")
            if ref:
                bound[ref] = rel.target_atom_id
    out: dict[str, Atom] = {}
    problems: list[str] = []
    for ref in refs:
        target = db.get(Atom, bound[ref]) if ref in bound else None
        if target is None:
            matches = [a for a in _candidates(db, ref) if a.status == "active"]
            if not matches:
                problems.append(f"No active atom goes by @{ref}.")
                continue
            if len(matches) > 1:
                problems.append(f"More than one atom goes by @{ref}. Rename one of them so each name is unique.")
                continue
            target = matches[0]
        if atom_id and target.id == atom_id:
            problems.append(f"@{ref} is this atom itself. A formula can only use other atoms.")
            continue
        if target.status != "active":
            problems.append(f"@{ref} ({target.values.get('name')}) is deprecated.")
            continue
        out[ref] = target
    seen: dict[uuid.UUID, str] = {}
    for ref, target in out.items():
        if target.id in seen:
            problems.append(f"@{seen[target.id]} and @{ref} are the same atom. Use one name for it.")
        seen[target.id] = ref
    return out, problems


def check(db: Session, atom_id: uuid.UUID | None, values: dict) -> None:
    """Raise ValidationFailed if the atom's formula (if any) can't be used."""
    key = formula_field(db)
    source = (values or {}).get(key) if key else None
    if not source:
        return
    try:
        _, refs = parse(source)
    except FormulaError as exc:
        raise ValidationFailed([field_error(key, "invalid_formula", exc.message)]) from exc
    if not link_type(db):
        raise ValidationFailed([field_error(key, "not_set_up", "The Calculated from relation type is missing. Run the seed command to add it.")])
    targets, problems = bind(db, atom_id, refs)
    if atom_id and not problems:
        from app.services.relations import _path_exists

        rt = link_type(db)
        for ref, target in targets.items():
            if _path_exists(db, rt, target.id, atom_id):
                problems.append(f"@{ref} is itself calculated from this atom, which would be a loop.")
    if problems:
        raise ValidationFailed([field_error(key, "invalid_formula", p) for p in problems])


def inputs(db: Session, atom_id: uuid.UUID | None, values: dict) -> list[dict]:
    """The atoms a (valid) formula would use, for showing next to it."""
    key = formula_field(db)
    source = (values or {}).get(key) if key else None
    if not source:
        return []
    _, refs = parse(source)
    targets, _ = bind(db, atom_id, refs)
    return [{"ref": r, "atom_id": str(t.id), "atom_name": t.values.get("name")} for r, t in targets.items()]


def sync_links(db: Session, atom: Atom, user_id) -> bool:
    """Make the atom's Calculated from links match its formula. Returns whether anything changed."""
    from app.services import relations as rel_service

    key = formula_field(db)
    source = atom.values.get(key) if key else None
    current = links(db, atom.id)
    wanted: dict[str, Atom] = {}
    if source:
        check(db, atom.id, atom.values)
        _, refs = parse(source)
        wanted, _ = bind(db, atom.id, refs)
    changed = False
    keep = set()
    for rel in current:
        ref = (rel.attributes or {}).get("ref")
        if ref in wanted and wanted[ref].id == rel.target_atom_id:
            keep.add(ref)
        else:
            db.delete(rel)
            changed = True
    db.flush()
    rt = link_type(db)
    for ref, target in wanted.items():
        if ref in keep:
            continue
        try:
            rel_service.add_link(db, rt, atom.id, target.id, {"ref": ref}, user_id, note=f"Used as @{ref} in the formula.")
        except AppError as exc:
            raise ValidationFailed([field_error(key, exc.code, exc.message)]) from exc
        changed = True
    return changed


# --- Evaluating on a form ---------------------------------------------------------------


def evaluate(source: str, inputs: dict, now=None):
    """Evaluate a formula with CEL-typed inputs keyed by ref. Returns the raw CEL result."""
    try:
        parsed, _ = parse(source)
    except FormulaError as exc:
        raise expressions.ExpressionError("invalid_formula", exc.message) from exc
    return expressions.evaluate(parsed, inputs, now)
