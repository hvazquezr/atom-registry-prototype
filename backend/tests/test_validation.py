import pytest

from app.errors import ValidationFailed
from app.services.validation import validate_atom_values

FIELDS = [
    {"key": "name", "label": "Name", "type": "text", "required": True, "config": {"max_length": 10}},
    {"key": "code", "label": "Code", "type": "text", "config": {"regex": "[A-Z]{3}-[0-9]{3}"}},
    {"key": "size", "label": "Size", "type": "number", "config": {"min": 1, "max": 100, "integer_only": True}},
    {"key": "since", "label": "Since", "type": "date", "config": {"min": "2020-01-01", "max": "2030-12-31"}},
    {"key": "kind", "label": "Kind", "type": "options", "config": {"choices": [{"value": "a"}, {"value": "b"}]}},
    {"key": "tags", "label": "Tags", "type": "options",
     "config": {"multiple": True, "max_selected": 2, "choices": [{"value": "x"}, {"value": "y"}, {"value": "z"}]}},
    {"key": "aliases", "label": "Aliases", "type": "list", "config": {"item_type": "text", "max_items": 2, "item": {"min_length": 2}}},
    {"key": "upper", "label": "Upper", "type": "text", "calculated": True, "expression": "name + '!'", "depends_on": ["name"]},
]


def errors_for(values, existing=None):
    with pytest.raises(ValidationFailed) as e:
        validate_atom_values(FIELDS, values, existing=existing)
    return {err["field"]: err["code"] for err in e.value.errors}


def test_valid_values_are_coerced_and_calculated():
    out = validate_atom_values(FIELDS, {"name": "Thing", "code": "ABC-123", "size": "12", "since": "2024-05-01",
                                        "kind": "a", "tags": ["z", "x"], "aliases": ["aa"]})
    assert out["size"] == 12
    assert out["tags"] == ["x", "z"]  # stored in the order the choices are defined
    assert out["upper"] == "Thing!"


def test_all_errors_are_reported_together():
    errs = errors_for({"code": "abc", "size": 0.5, "since": "2019-01-01", "kind": "c", "tags": ["x", "y", "z"],
                       "aliases": ["a", "bb", "cc"], "nope": 1, "upper": "x"})
    assert errs == {
        "name": "required", "code": "invalid", "size": "invalid", "since": "invalid", "kind": "invalid",
        "tags": "invalid", "aliases": "invalid", "nope": "unknown_field", "upper": "read_only",
    }


def test_text_length_and_bad_types():
    assert errors_for({"name": "x" * 11}) == {"name": "invalid"}
    assert errors_for({"name": "ok", "size": "lots"}) == {"size": "invalid"}
    assert errors_for({"name": "ok", "since": "25/12/2024"}) == {"since": "invalid"}


def test_partial_update_merges_with_existing_and_null_clears():
    existing = validate_atom_values(FIELDS, {"name": "Old", "size": 5})
    out = validate_atom_values(FIELDS, {"name": "New", "size": None}, existing=existing)
    assert out["name"] == "New" and "size" not in out and out["upper"] == "New!"
    assert errors_for({"name": None}, existing=existing) == {"name": "required"}


def test_regex_uses_full_match():
    assert errors_for({"name": "ok", "code": "XABC-123"}) == {"code": "invalid"}
