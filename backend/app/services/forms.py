"""Forms: atoms grouped into ordered sections.

A form has a draft (sections and items) that editors change freely. Publishing opens a change
request; when a reviewer approves it, the draft is resolved against the atoms as they are at
that moment and frozen into a form_version. Published versions never change: they pin each
atom's revision, the input rules derived from its values, and the Required if conditions.

How relations shape a form (for an item whose atom is A):
  * A "Depends on" D      D must be on the form too (publishing is blocked otherwise).
  * P "Includes" A        A is shown grouped under P when both are on the form.
  * A "Required if" T     A is hidden and optional until T's answer meets the link's condition
                          (a CEL expression over `value`; empty means "T is answered"). Then A
                          is shown and required. T must be on the form.
  * A "Calculated from" I A's answer comes from its formula (see formulas.py), so it is read-only.
                          It is calculated once every input I is answered. I must be on the form.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

import re2
from celpy import celtypes
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.errors import AppError, ValidationFailed, bad_request, conflict, field_error, not_found
from app.models import AppSetting, Atom, AtomRelation, ChangeRequest, Form, FormItem, FormSection, FormVersion, User
from app.services import expressions, formulas
from app.services.fields import KEY_PATTERN, check_config
from app.services.validation import is_empty, validate_value

FORM_KINDS = ("publish_form", "deprecate_form", "restore_form")
ANSWER_TYPES = ("text", "integer", "number", "date", "boolean", "choice", "multiple_choice")

# --- How atom values become form inputs --------------------------------------------------

INPUT_SETTINGS_KEY = "form_inputs"
DEFAULT_INPUT_SETTINGS = {
    # An atom field whose value says what kind of answer the atom takes, and how its values map
    # to answer types. Atoms without a mapped value use default_type.
    "type_field": "data_type",
    "type_map": {"string": "text", "integer": "integer", "decimal": "number", "date": "date", "boolean": "boolean"},
    "default_type": "text",
    # Atom fields that hold answer rules. Leave empty when the registry has no such field.
    "min_length_field": None,
    "max_length_field": "max_length",
    "min_field": None,
    "max_field": None,
    "regex_field": None,
    # A list field holding the allowed answers. When set and non-empty, the atom becomes a choice.
    "choices_field": None,
    # A text field holding the atom's answer formula (see formulas.py). Atoms with one are calculated on forms.
    "formula_field": "formula",
}
_SETTING_FIELDS = ("type_field", "min_length_field", "max_length_field", "min_field", "max_field", "regex_field", "choices_field",
                   "formula_field")


def input_settings(db: Session) -> dict:
    row = db.get(AppSetting, INPUT_SETTINGS_KEY)
    return DEFAULT_INPUT_SETTINGS | (row.value if row else {})


def save_input_settings(db: Session, data: dict) -> dict:
    merged = input_settings(db) | {k: v for k, v in data.items() if k in DEFAULT_INPUT_SETTINGS}
    errors = []
    for k in _SETTING_FIELDS:
        v = merged.get(k)
        if v in ("", None):
            merged[k] = None
        elif not isinstance(v, str) or not KEY_PATTERN.fullmatch(v):
            errors.append(field_error(k, "invalid", "Use a field key (lowercase letters, digits and _)."))
    if merged.get("default_type") not in ANSWER_TYPES or merged["default_type"] in ("choice", "multiple_choice"):
        errors.append(field_error("default_type", "invalid", "Default type must be text, integer, number, date or boolean."))
    tm = merged.get("type_map")
    if not isinstance(tm, dict) or any(not isinstance(k, str) or v not in ANSWER_TYPES for k, v in tm.items()):
        errors.append(field_error("type_map", "invalid", f"Each value must map to one of {', '.join(ANSWER_TYPES)}."))
    if errors:
        raise ValidationFailed(errors)
    row = db.get(AppSetting, INPUT_SETTINGS_KEY)
    if not row:
        row = AppSetting(key=INPUT_SETTINGS_KEY, value={})
        db.add(row)
    row.value = merged
    db.flush()
    return merged


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def input_spec(values: dict, settings: dict) -> dict:
    """Derive the input an atom gets on a form from the atom's own values."""

    def val(setting):
        key = settings.get(setting)
        return values.get(key) if key else None

    raw_type = val("type_field")
    answer_type = (settings.get("type_map") or {}).get(str(raw_type)) if raw_type is not None else None
    answer_type = answer_type or settings.get("default_type") or "text"
    choices = val("choices_field")
    if isinstance(choices, list) and choices and answer_type not in ("choice", "multiple_choice"):
        answer_type = "choice"

    config: dict = {}
    if answer_type in ("choice", "multiple_choice"):
        items = [str(c) for c in choices] if isinstance(choices, list) else []
        config = {"choices": [{"value": c, "label": c} for c in dict.fromkeys(items)] or [{"value": "yes", "label": "Yes"}]}
        if answer_type == "multiple_choice":
            config["multiple"] = True
        return {"type": "options", "answer_type": answer_type, "config": config}
    if answer_type == "text":
        lo, hi = _num(val("min_length_field")), _num(val("max_length_field"))
        if lo is not None and lo >= 0:
            config["min_length"] = int(lo)
        if hi is not None and hi >= 0:
            config["max_length"] = int(hi)
        regex = val("regex_field")
        if isinstance(regex, str) and regex:
            try:
                re2.compile(regex)
                config["regex"] = regex
            except re2.error:
                pass
    elif answer_type in ("integer", "number"):
        lo, hi = _num(val("min_field")), _num(val("max_field"))
        if lo is not None:
            config["min"] = lo
        if hi is not None:
            config["max"] = hi
        if answer_type == "integer":
            config["integer_only"] = True
    elif answer_type == "date":
        for k, setting in (("min", "min_field"), ("max", "max_field")):
            v = val(setting)
            if isinstance(v, str):
                try:
                    dt.date.fromisoformat(v)
                    config[k] = v
                except ValueError:
                    pass
    field_type = {"integer": "number"}.get(answer_type, answer_type)
    if field_type != "boolean" and check_config(field_type, config):
        config = {"integer_only": True} if answer_type == "integer" else {}
    return {"type": field_type, "answer_type": answer_type, "config": config}


# --- Reading ----------------------------------------------------------------------------


def get_form(db: Session, id_or_key: str | uuid.UUID) -> Form:
    form = None
    try:
        form = db.get(Form, uuid.UUID(str(id_or_key)))
    except ValueError:
        form = db.scalars(select(Form).where(Form.key == str(id_or_key))).first()
    if not form:
        raise not_found("Form")
    return form


def get_version(db: Session, form: Form, version: int | str) -> FormVersion:
    if version == "latest":
        if not form.latest_version:
            raise not_found("Published version")
        version = form.latest_version
    try:
        version = int(version)
    except (TypeError, ValueError) as exc:
        raise bad_request("bad_version", "version must be a number, latest or draft.") from exc
    fv = db.scalars(select(FormVersion).where(FormVersion.form_id == form.id, FormVersion.version == version)).first()
    if not fv:
        raise not_found(f"Version {version}")
    return fv


def list_versions(db: Session, form: Form) -> list[FormVersion]:
    return list(db.scalars(select(FormVersion).where(FormVersion.form_id == form.id).order_by(FormVersion.version.desc())))


def pending_request(db: Session, form: Form) -> ChangeRequest | None:
    return db.scalars(
        select(ChangeRequest).where(ChangeRequest.form_id == form.id, ChangeRequest.status == "pending")
        .order_by(ChangeRequest.submitted_at)
    ).first()


def search(db: Session, q: str | None = None, status: str = "active", atom_id: uuid.UUID | None = None,
           page: int = 1, page_size: int = 25) -> dict:
    query = select(Form)
    if status and status != "all":
        query = query.where(Form.status == status)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(Form.name.ilike(like), Form.key.ilike(like), Form.description.ilike(like)))
    if atom_id:
        query = query.where(Form.id.in_(select(FormItem.form_id).where(FormItem.atom_id == atom_id)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page, page_size = max(1, page), max(1, min(page_size, 100))
    rows = db.scalars(query.order_by(Form.name, Form.id).offset((page - 1) * page_size).limit(page_size)).all()
    counts = dict(db.execute(
        select(FormItem.form_id, func.count()).where(FormItem.form_id.in_([f.id for f in rows])).group_by(FormItem.form_id)
    ).all()) if rows else {}
    return {"items": [summary(f, counts.get(f.id, 0)) for f in rows], "total": total, "page": page, "page_size": page_size}


def summary(form: Form, item_count: int | None = None) -> dict:
    d = {
        "id": str(form.id), "key": form.key, "name": form.name, "description": form.description,
        "status": form.status, "draft_revision": form.draft_revision, "latest_version": form.latest_version,
        "created_at": form.created_at.isoformat() if form.created_at else None,
        "updated_at": form.updated_at.isoformat() if form.updated_at else None,
    }
    if item_count is not None:
        d["item_count"] = item_count
    return d


def draft_body(form: Form) -> dict:
    return {
        "sections": [
            {
                "id": str(s.id), "title": s.title, "description": s.description,
                "items": [
                    {"id": str(i.id), "atom_id": str(i.atom_id), "key": i.key, "label": i.label,
                     "help_text": i.help_text, "required": i.required}
                    for i in s.items
                ],
            }
            for s in form.sections
        ]
    }


# --- Writing the draft ------------------------------------------------------------------

_SLUG = re.compile(r"[^a-z0-9]+")


def slug_key(name: str) -> str:
    s = _SLUG.sub("_", (name or "").lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "q_" + s if s else "question"
    return s[:64]


def _check_key(key: str, where: str, errors: list) -> None:
    if not KEY_PATTERN.fullmatch(key or ""):
        errors.append(field_error(where, "invalid", "Keys start with a letter and use only lowercase letters, digits and _."))


def create_form(db: Session, user: User, key: str, name: str, description: str = "", sections: list | None = None) -> Form:
    errors = []
    _check_key(key, "key", errors)
    if not (name or "").strip():
        errors.append(field_error("name", "required", "Name is required."))
    if errors:
        raise ValidationFailed(errors)
    if db.scalars(select(Form).where(Form.key == key)).first():
        raise conflict("duplicate_key", f"A form with key {key!r} already exists.")
    form = Form(key=key, name=name.strip(), description=description or "", created_by=user.id, updated_by=user.id)
    db.add(form)
    db.flush()
    _replace_sections(db, form, sections if sections is not None else [{"title": "", "items": []}])
    db.flush()
    return form


def _replace_sections(db: Session, form: Form, sections: list) -> None:
    if not isinstance(sections, list) or not sections:
        raise ValidationFailed([field_error("sections", "required", "A form needs at least one section.")])
    errors: list[dict] = []
    seen_atoms: set[uuid.UUID] = set()
    seen_keys: set[str] = set()
    parsed = []
    atom_ids = []
    for si, s in enumerate(sections):
        for ii, it in enumerate((s or {}).get("items") or []):
            try:
                atom_ids.append(uuid.UUID(str(it.get("atom_id"))))
            except (ValueError, AttributeError):
                errors.append(field_error(f"sections[{si}].items[{ii}].atom_id", "invalid", "atom_id must be a UUID."))
    atoms = {a.id: a for a in db.scalars(select(Atom).where(Atom.id.in_(atom_ids)))} if atom_ids else {}
    for si, s in enumerate(sections):
        s = s or {}
        title = (s.get("title") or "").strip()
        if len(title) > 200:
            errors.append(field_error(f"sections[{si}].title", "too_long", "Section titles are at most 200 characters."))
        items = []
        for ii, it in enumerate(s.get("items") or []):
            where = f"sections[{si}].items[{ii}]"
            try:
                aid = uuid.UUID(str(it.get("atom_id")))
            except (ValueError, AttributeError):
                continue
            atom = atoms.get(aid)
            if not atom:
                errors.append(field_error(f"{where}.atom_id", "unknown_atom", "The atom does not exist."))
                continue
            if aid in seen_atoms:
                errors.append(field_error(f"{where}.atom_id", "duplicate_atom", f"{atom.values.get('name')!r} is on the form twice."))
                continue
            seen_atoms.add(aid)
            key = (it.get("key") or "").strip() or None
            if key:
                _check_key(key, f"{where}.key", errors)
                if key in seen_keys:
                    errors.append(field_error(f"{where}.key", "duplicate_key", f"The key {key!r} is used twice."))
                seen_keys.add(key)
            label = (it.get("label") or "").strip()
            if len(label) > 200:
                errors.append(field_error(f"{where}.label", "too_long", "Labels are at most 200 characters."))
            items.append({"atom": atom, "key": key, "label": label, "help_text": (it.get("help_text") or "").strip(),
                          "required": bool(it.get("required"))})
        parsed.append({"title": title, "description": (s.get("description") or "").strip(), "items": items})
    if errors:
        raise ValidationFailed(errors)
    # Give items without a key one from the atom's name.
    for s in parsed:
        for it in s["items"]:
            if not it["key"]:
                base = slug_key(str(it["atom"].values.get("name") or "question"))
                key, n = base, 2
                while key in seen_keys:
                    key = f"{base[:60]}_{n}"
                    n += 1
                it["key"] = key
                seen_keys.add(key)

    form.sections.clear()
    db.flush()
    position = 0
    for si, s in enumerate(parsed):
        section = FormSection(form_id=form.id, title=s["title"], description=s["description"], position=si)
        form.sections.append(section)
        db.flush()
        for it in s["items"]:
            section.items.append(FormItem(form_id=form.id, section_id=section.id, atom_id=it["atom"].id, key=it["key"],
                                          position=position, label=it["label"], help_text=it["help_text"],
                                          required=it["required"]))
            position += 1
    db.flush()


def save_draft(db: Session, user: User, form: Form, data: dict) -> Form:
    if form.status != "active":
        raise conflict("deprecated", "Restore the form before changing it.")
    base = data.get("base_revision")
    if base is not None and base != form.draft_revision:
        raise conflict("stale", "Someone else changed this form since you loaded it.", {"current_revision": form.draft_revision})
    if "name" in data and data["name"] is not None:
        if not data["name"].strip():
            raise ValidationFailed([field_error("name", "required", "Name is required.")])
        form.name = data["name"].strip()
    if data.get("description") is not None:
        form.description = data["description"]
    if data.get("sections") is not None:
        _replace_sections(db, form, data["sections"])
    form.draft_revision += 1
    form.updated_by = user.id
    db.flush()
    db.refresh(form)
    return form


# --- Resolving and checking -------------------------------------------------------------


def _relations_touching(db: Session, atom_ids: list[uuid.UUID]) -> list[AtomRelation]:
    if not atom_ids:
        return []
    return list(db.scalars(
        select(AtomRelation).where(or_(AtomRelation.source_atom_id.in_(atom_ids), AtomRelation.target_atom_id.in_(atom_ids)))
        .order_by(AtomRelation.created_at)
    ))


def _issue(severity: str, code: str, message: str, item: str | None = None, add_atom: Atom | None = None) -> dict:
    d = {"severity": severity, "code": code, "message": message, "item": item}
    if add_atom is not None:
        d["add_atom_id"] = str(add_atom.id)
        d["add_atom_name"] = add_atom.values.get("name")
    return d


def resolve(db: Session, form: Form) -> tuple[dict, list[dict]]:
    """Resolve the draft against the current atoms. Returns (definition, issues)."""
    settings = input_settings(db)
    items = [i for s in form.sections for i in s.items]
    atom_ids = [i.atom_id for i in items]
    rels = _relations_touching(db, atom_ids)
    other_ids = {r.source_atom_id for r in rels} | {r.target_atom_id for r in rels}
    atoms = {a.id: a for a in db.scalars(select(Atom).where(Atom.id.in_(set(atom_ids) | other_ids)))} if items else {}
    key_of = {i.atom_id: i.key for i in items}
    order = {i.key: n for n, i in enumerate(items)}
    issues: list[dict] = []
    seen_suggestions: set[tuple] = set()

    def name(aid):
        a = atoms.get(aid)
        return (a.values.get("name") if a else None) or "an atom"

    def suggest(severity, code, message, item, atom):
        if (code, atom.id) in seen_suggestions and code != "missing_trigger":
            return
        seen_suggestions.add((code, atom.id))
        issues.append(_issue(severity, code, message, item, atom if atom.status == "active" else None))

    rules: dict[str, dict] = {i.key: {"conditions": [], "parent": None, "depends_on": [], "includes": [], "inputs": []} for i in items}
    for r in rels:
        rt = r.relation_type
        if rt.status != "active":
            continue
        src, tgt = r.source_atom_id, r.target_atom_id
        sk, tk = key_of.get(src), key_of.get(tgt)
        if rt.key == "dependency" and sk:
            if tk:
                rules[sk]["depends_on"].append(tk)
            elif atoms.get(tgt):
                suggest("error", "missing_dependency", f"{name(src)} depends on {name(tgt)}, which isn't on the form.", sk, atoms[tgt])
        elif rt.key == "includes" and sk:
            if tk:
                rules[sk]["includes"].append(tk)
                if rules[tk]["parent"] is None:
                    rules[tk]["parent"] = sk
            elif atoms.get(tgt):
                suggest("warning", "missing_included", f"{name(src)} includes {name(tgt)}, which isn't on the form.", sk, atoms[tgt])
        elif rt.key == "required_if" and sk:
            condition = (r.attributes or {}).get("condition") or None
            if tk:
                rules[sk]["conditions"].append({"when": tk, "atom_id": str(tgt), "atom_name": name(tgt),
                                                "condition": condition, "note": r.note or ""})
            elif atoms.get(tgt):
                suggest("error", "missing_trigger",
                        f"{name(src)} is required if {name(tgt)} is answered, but {name(tgt)} isn't on the form.", sk, atoms[tgt])
        elif rt.key == formulas.LINK_TYPE and sk:
            ref = (r.attributes or {}).get("ref")
            if tk:
                rules[sk]["inputs"].append({"ref": ref, "key": tk, "atom_id": str(tgt), "atom_name": name(tgt)})
            elif atoms.get(tgt):
                suggest("error", "missing_input", f"{name(src)} is calculated from {name(tgt)}, which isn't on the form.", sk, atoms[tgt])

    formula_key = settings.get("formula_field")
    sections_out = []
    for s in form.sections:
        if not s.items:
            issues.append(_issue("warning", "empty_section", f"Section {s.title or '(untitled)'!r} has no atoms."))
        out_items = []
        for i in s.items:
            atom = atoms.get(i.atom_id)
            values = dict(atom.values) if atom else {}
            r = rules[i.key]
            if atom and atom.status != "active":
                issues.append(_issue("error", "atom_deprecated", f"{name(i.atom_id)} is deprecated. Remove it or restore the atom.", i.key))
            if i.required and r["conditions"]:
                issues.append(_issue("error", "required_conditional",
                                     f"{i.label or name(i.atom_id)} is Required if another answer, so it can't also be always required.", i.key))
            for c in r["conditions"]:
                if order[c["when"]] > order[i.key]:
                    issues.append(_issue("warning", "trigger_after",
                                         f"{c['atom_name']} comes after {i.label or name(i.atom_id)}, which depends on its answer.", i.key))
                if c["condition"] and (msg := _condition_error(c["condition"])):
                    issues.append(_issue("error", "invalid_condition", f"The condition on {name(i.atom_id)} is invalid: {msg}", i.key))
            calculation = None
            formula = values.get(formula_key) if formula_key else None
            if formula:
                calculation = {"formula": formula, "inputs": r["inputs"]}
                label = i.label or name(i.atom_id)
                try:
                    _, refs = formulas.parse(formula)
                except formulas.FormulaError as exc:
                    refs = None
                    issues.append(_issue("error", "invalid_formula", f"The formula of {name(i.atom_id)} is invalid: {exc.message}", i.key))
                linked = {inp["ref"] for inp in r["inputs"]}
                if refs is not None and any(ref not in linked for ref in refs) and not any(
                        x["code"] == "missing_input" and x["item"] == i.key for x in issues):
                    issues.append(_issue("error", "unbound_formula",
                                         f"The formula of {name(i.atom_id)} uses atoms it isn't linked to. Save the atom again to fix its links.", i.key))
                if i.required:
                    issues.append(_issue("error", "required_calculated", f"{label} is calculated, so it can't also be always required.", i.key))
                if r["conditions"]:
                    issues.append(_issue("error", "conditional_calculated", f"{label} is calculated, so it can't be Required if another answer.", i.key))
            out_item = {
                "key": i.key,
                "atom_id": str(i.atom_id),
                "atom_revision": atom.revision if atom else None,
                "atom_name": values.get("name"),
                "atom_status": atom.status if atom else None,
                "label": i.label or values.get("name") or i.key,
                "help_text": i.help_text or values.get("description") or "",
                "overrides": {"label": i.label, "help_text": i.help_text, "required": i.required},
                "required": i.required,
                "input": input_spec(values, settings),
                "atom_values": values,
                "conditions": r["conditions"],
                "parent": r["parent"],
                "depends_on": r["depends_on"],
            }
            if calculation:
                out_item["calculation"] = calculation
            out_item["requirement"] = requirement(out_item)
            out_items.append(out_item)
        sections_out.append({"title": s.title, "description": s.description, "items": out_items})

    if not items:
        issues.append(_issue("error", "empty_form", "Add at least one atom to the form."))
    for key in _never_shown([it for s in sections_out for it in s["items"]]):
        issues.append(_issue("error", "never_shown",
                             f"{key} is only Required if answers that are themselves never shown (a loop), so it would never appear.", key))
    definition = {
        "form": {"id": str(form.id), "key": form.key, "name": form.name, "description": form.description},
        "sections": sections_out,
    }
    return definition, issues


def _condition_error(source: str) -> str | None:
    from app.services.relations import check_condition

    return check_condition(source)


def _never_shown(items: list[dict]) -> list[str]:
    reachable = {i["key"] for i in items if not i["conditions"]}
    changed = True
    while changed:
        changed = False
        for i in items:
            if i["key"] not in reachable and any(c["when"] in reachable for c in i["conditions"]):
                reachable.add(i["key"])
                changed = True
    return [i["key"] for i in items if i["key"] not in reachable]


def requirement(item: dict) -> str:
    """How the form treats an answer: required, optional, required_if (a Required if link) or calculated."""
    if item.get("calculation"):
        return "calculated"
    if item.get("conditions"):
        return "required_if"
    return "required" if item.get("required") else "optional"


def with_requirements(definition: dict) -> dict:
    """Fill in `requirement` on versions published before it existed."""
    for s in definition.get("sections", []):
        for i in s.get("items", []):
            i.setdefault("requirement", requirement(i))
    return definition


def has_errors(issues: list[dict]) -> bool:
    return any(i["severity"] == "error" for i in issues)


def atoms_updated(db: Session, fv: FormVersion | None) -> list[dict]:
    """Items whose atom has changed since the version was published."""
    if not fv:
        return []
    items = [i for s in fv.definition["sections"] for i in s["items"]]
    ids = [uuid.UUID(i["atom_id"]) for i in items]
    current = dict(db.execute(select(Atom.id, Atom.revision).where(Atom.id.in_(ids))).all()) if ids else {}
    return [
        {"key": i["key"], "atom_id": i["atom_id"], "atom_name": i["atom_name"], "pinned_revision": i["atom_revision"],
         "current_revision": current.get(uuid.UUID(i["atom_id"]))}
        for i in items if current.get(uuid.UUID(i["atom_id"])) not in (None, i["atom_revision"])
    ]


def _comparable(definition: dict) -> dict:
    """The parts of a definition that matter when deciding whether anything changed."""
    return {
        "form": {k: v for k, v in definition["form"].items() if k != "id"},
        "sections": [
            {"title": s["title"], "description": s["description"],
             "items": [{k: v for k, v in i.items() if k != "atom_status"} for i in s["items"]]}
            for s in definition["sections"]
        ],
    }


def diff(old: dict | None, new: dict) -> dict:
    """What changed between two definitions, by item key."""
    def by_key(d):
        return {i["key"]: i for s in (d or {}).get("sections", []) for i in s["items"]}

    a, b = by_key(old), by_key(new)
    changed = []
    for k in a.keys() & b.keys():
        what = []
        if a[k]["atom_revision"] != b[k]["atom_revision"]:
            what.append(f"atom revision {a[k]['atom_revision']} → {b[k]['atom_revision']}")
        for f in ("label", "help_text", "required", "input", "conditions", "calculation"):
            if a[k].get(f) != b[k].get(f):
                what.append(f.replace("_", " "))
        if what:
            changed.append({"key": k, "label": b[k]["label"], "changes": what})
    return {
        "added": [{"key": k, "label": b[k]["label"]} for k in b if k not in a],
        "removed": [{"key": k, "label": a[k]["label"]} for k in a if k not in b],
        "changed": sorted(changed, key=lambda c: c["key"]),
        "sections_changed": bool(old) and [s["title"] for s in old["sections"]] != [s["title"] for s in new["sections"]],
        "details_changed": bool(old) and (old["form"]["name"], old["form"]["description"]) != (new["form"]["name"], new["form"]["description"]),
    }


# --- Answers ----------------------------------------------------------------------------


def _clean_answer(item: dict, value) -> tuple[object, list[str]]:
    spec = item["input"]
    if spec["type"] == "boolean":
        if isinstance(value, bool):
            return value, []
        if isinstance(value, str) and value.lower() in ("true", "false", "yes", "no"):
            return value.lower() in ("true", "yes"), []
        return value, ["must be true or false"]
    try:
        return validate_value({"type": spec["type"], "config": spec.get("config") or {}}, value)
    except ValueError as exc:
        return value, [str(exc)]


def _condition_met(condition: str | None, trigger: dict, value) -> bool:
    if not condition:
        return True
    spec = trigger["input"]
    parsed = expressions.parse(condition)
    result = expressions.evaluate(parsed, {"value": expressions.to_cel(value, spec["type"], spec.get("config"))})
    if not isinstance(result, (celtypes.BoolType, bool)):
        raise expressions.ExpressionError("type", "The condition must return true or false.")
    return bool(result)


def _calculate(items: list[dict], by_key: dict, clean: dict, errors: dict, now=None) -> None:
    """Fill in calculated answers, in an order where inputs come first. Inputs that are calculated
    themselves are fine (links can't loop). An item with an unanswered input stays empty."""
    pending = [i for i in items if i.get("calculation")]
    for _ in range(len(pending) + 1):
        if not pending:
            return
        waiting = []
        for i in pending:
            inputs = i["calculation"]["inputs"]
            if any(by_key[inp["key"]].get("calculation") and inp["key"] not in clean and
                   any(p["key"] == inp["key"] for p in pending) for inp in inputs):
                waiting.append(i)
                continue
            if any(inp["key"] not in clean for inp in inputs):
                continue
            spec = i["input"]
            try:
                variables = {inp["ref"]: expressions.to_cel(clean[inp["key"]], by_key[inp["key"]]["input"]["type"],
                                                            by_key[inp["key"]]["input"].get("config"))
                             for inp in inputs}
                result = formulas.evaluate(i["calculation"]["formula"], variables, now)
                if spec["type"] == "boolean":
                    if not isinstance(result, (celtypes.BoolType, bool)):
                        raise expressions.ExpressionError("type", "the formula must return true or false")
                    value = bool(result)
                else:
                    value = expressions.from_cel(result, spec["type"], spec.get("config"))
            except expressions.ExpressionError as exc:
                errors[i["key"]] = exc.message
                continue
            value, msgs = _clean_answer(i, value)
            if msgs:
                errors[i["key"]] = f"the result {value!r} " + "; ".join(msgs)
                continue
            clean[i["key"]] = value
        if len(waiting) == len(pending):
            return
        pending = waiting


def validate_answers(definition: dict, answers: dict, now=None) -> dict:
    """Check a set of answers against a form definition.

    Returns which items are visible and required, the cleaned answers and every error. Answers
    to items that are hidden are not errors; they're listed under `ignored`. Calculated items
    get their answer from their formula; answers sent for them are ignored too.
    """
    if not isinstance(answers, dict):
        raise bad_request("bad_answers", "answers must be an object keyed by item key.")
    items = [i for s in definition["sections"] for i in s["items"]]
    by_key = {i["key"]: i for i in items}
    calculated = {i["key"] for i in items if i.get("calculation")}
    errors: list[dict] = []
    clean: dict = {}
    for key, value in answers.items():
        if key not in by_key:
            errors.append(field_error(key, "unknown_field", f"This form has no question {key!r}."))
            continue
        if is_empty(value) or key in calculated:
            continue
        v, msgs = _clean_answer(by_key[key], value)
        if msgs:
            errors.append(field_error(key, "invalid", f"{by_key[key]['label']} " + "; ".join(msgs) + "."))
        else:
            clean[key] = v

    calc_errors: dict[str, str] = {}
    _calculate(items, by_key, clean, calc_errors, now)
    visible = {i["key"] for i in items if not i["conditions"]}
    condition_errors: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for i in items:
            if i["key"] in visible:
                continue
            for c in i["conditions"]:
                if c["when"] not in visible or c["when"] not in clean:
                    continue
                try:
                    met = _condition_met(c["condition"], by_key[c["when"]], clean[c["when"]])
                except expressions.ExpressionError as exc:
                    condition_errors[i["key"]] = exc.message
                    continue
                if met:
                    visible.add(i["key"])
                    changed = True
                    break

    fields = []
    hidden = {k for k in by_key if k not in visible}
    if any(k in clean for k in hidden):
        # Answers to hidden questions don't count, so calculate again without them.
        for k in hidden | calculated:
            clean.pop(k, None)
        calc_errors.clear()
        _calculate(items, by_key, clean, calc_errors, now)
    for i in items:
        shown = i["key"] in visible
        is_calc = i["key"] in calculated
        required = shown and not is_calc and (i["required"] or bool(i["conditions"]))
        f = {"key": i["key"], "visible": shown, "required": required}
        if is_calc:
            f["calculated"] = True
            f["value"] = clean.get(i["key"])
        fields.append(f)
        if i["key"] in calc_errors:
            errors.append(field_error(i["key"], "calculation_failed", f"{i['label']} could not be calculated: {calc_errors[i['key']]}"))
        if required and i["key"] not in clean and not any(e["field"] == i["key"] for e in errors):
            errors.append(field_error(i["key"], "required", f"{i['label']} is required."))
        if i["key"] in condition_errors:
            errors.append(field_error(i["key"], "condition_failed", f"The condition for {i['label']} failed: {condition_errors[i['key']]}"))
    ignored = sorted(k for k, v in answers.items() if (k in hidden or k in calculated) and k in by_key and not is_empty(v))
    for k in ignored:
        if k not in calculated:
            clean.pop(k, None)
    errors = [e for e in errors if e["field"] not in hidden or e["code"] == "condition_failed"]
    return {"valid": not errors, "answers": clean, "fields": fields, "errors": errors, "ignored": ignored}


# --- Reviewed changes -------------------------------------------------------------------


def submit_request(db: Session, user: User, form: Form, kind: str, notes: str = "", comment: str = "") -> ChangeRequest:
    if kind not in FORM_KINDS:
        raise bad_request("bad_kind", f"kind must be one of {', '.join(FORM_KINDS)}.")
    if pending_request(db, form):
        raise conflict("pending_request", "This form already has a change request waiting for review.")
    if kind == "publish_form":
        if form.status != "active":
            raise conflict("deprecated", "Restore the form before publishing it.")
        definition, issues = resolve(db, form)
        if has_errors(issues):
            raise ValidationFailed([field_error(i["item"] or "form", i["code"], i["message"]) for i in issues if i["severity"] == "error"])
        latest = get_version(db, form, form.latest_version) if form.latest_version else None
        if latest and _comparable(latest.definition) == _comparable(definition):
            raise conflict("no_changes", "Nothing has changed since the last published version.")
    elif kind == "deprecate_form" and form.status == "deprecated":
        raise conflict("already_deprecated", "The form is already deprecated.")
    elif kind == "restore_form" and form.status == "active":
        raise conflict("already_active", "The form is already active.")
    cr = ChangeRequest(form_id=form.id, kind=kind, proposed_values={"notes": notes or ""} if kind == "publish_form" else {},
                       proposed_relations={}, base_revision=form.draft_revision, comment=comment or "", submitted_by=user.id)
    db.add(cr)
    db.flush()
    return cr


def revise_request(db: Session, user: User, cr: ChangeRequest, notes: str | None, comment: str | None) -> ChangeRequest:
    """The submitter moves a pending publish request onto the current draft."""
    if cr.status != "pending":
        raise conflict("not_pending", "Only pending change requests can be edited.")
    if cr.submitted_by != user.id:
        raise AppError(403, "forbidden", "Only the person who submitted a change request can edit it.")
    form = get_form(db, cr.form_id)
    if cr.kind == "publish_form":
        _, issues = resolve(db, form)
        if has_errors(issues):
            raise ValidationFailed([field_error(i["item"] or "form", i["code"], i["message"]) for i in issues if i["severity"] == "error"])
        if notes is not None:
            cr.proposed_values = {"notes": notes}
    if comment is not None:
        cr.comment = comment
    cr.base_revision = form.draft_revision
    db.flush()
    return cr


def apply_request(db: Session, reviewer: User, cr: ChangeRequest) -> Form:
    """Called on approval: re-check, then publish, deprecate or restore."""
    form = db.execute(select(Form).where(Form.id == cr.form_id).with_for_update()).scalar_one()
    if cr.base_revision is not None and form.draft_revision != cr.base_revision:
        raise conflict("stale", "The form's draft changed after this request was submitted. The submitter needs to update the request.",
                       {"base_revision": cr.base_revision, "current_revision": form.draft_revision})
    if cr.kind == "publish_form":
        definition, issues = resolve(db, form)
        if has_errors(issues):
            raise ValidationFailed([field_error(i["item"] or "form", i["code"], i["message"]) for i in issues if i["severity"] == "error"])
        version = (form.latest_version or 0) + 1
        definition["version"] = version
        definition["published_at"] = dt.datetime.now(dt.UTC).isoformat()
        db.add(FormVersion(form_id=form.id, version=version, definition=definition, notes=(cr.proposed_values or {}).get("notes", ""),
                           change_request_id=cr.id, published_by=cr.submitted_by, approved_by=reviewer.id))
        form.latest_version = version
    elif cr.kind == "deprecate_form":
        form.status = "deprecated"
    elif cr.kind == "restore_form":
        form.status = "active"
    db.flush()
    return form


def request_preview(db: Session, cr: ChangeRequest) -> dict | None:
    """For a publish request: what would be published and how it differs from the latest version."""
    if cr.kind != "publish_form" or not cr.form_id:
        return None
    form = get_form(db, cr.form_id)
    if cr.status == "approved":
        fv = db.scalars(select(FormVersion).where(FormVersion.change_request_id == cr.id)).first()
        if fv:
            prev = get_version(db, form, fv.version - 1).definition if fv.version > 1 else None
            return {"version": fv.version, "definition": fv.definition, "issues": [], "diff": diff(prev, fv.definition)}
    definition, issues = resolve(db, form)
    latest = get_version(db, form, form.latest_version).definition if form.latest_version else None
    return {"version": (form.latest_version or 0) + 1, "definition": definition, "issues": issues, "diff": diff(latest, definition)}


def version_summary(fv: FormVersion, names: dict | None = None) -> dict:
    items = [i for s in fv.definition["sections"] for i in s["items"]]
    return {
        "version": fv.version, "notes": fv.notes, "item_count": len(items),
        "published_at": fv.published_at.isoformat() if fv.published_at else None,
        "published_by": str(fv.published_by) if fv.published_by else None,
        "published_by_name": (names or {}).get(fv.published_by),
        "approved_by_name": (names or {}).get(fv.approved_by),
        "change_request_id": str(fv.change_request_id) if fv.change_request_id else None,
    }
