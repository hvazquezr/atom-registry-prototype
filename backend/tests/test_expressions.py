import datetime as dt

import pytest

from app.services import expressions as ex
from app.services.validation import validate_atom_values


def test_parse_finds_variables_and_ignores_macro_variables():
    p = ex.parse('size(tags) > 0 && tags.exists(t, t == "pii") && price * 2 > 10')
    assert p.variables == {"tags", "price"}
    assert "exists" in p.functions and "size" in p.functions


def test_unknown_function_and_syntax_errors():
    with pytest.raises(ex.ExpressionError) as e:
        ex.parse("system('rm -rf /')")
    assert e.value.code == "unknown_function"
    with pytest.raises(ex.ExpressionError) as e:
        ex.parse("price *")
    assert e.value.code == "syntax"


def test_length_and_complexity_limits():
    with pytest.raises(ex.ExpressionError) as e:
        ex.parse("a + " * 1000 + "a")
    assert e.value.code in ("too_long", "too_complex")


def test_today_and_days_between_use_given_time():
    now = dt.datetime(2026, 11, 25, 15, 0, tzinfo=dt.UTC)
    p = ex.parse("days_between(start, today())")
    result = ex.evaluate(p, {"start": ex.to_cel("2026-11-20", "date")}, now=now)
    assert ex.from_cel(result, "number") == 5
    assert ex.from_cel(ex.evaluate(ex.parse("today()"), {}, now=now), "date") == "2026-11-25"
    assert ex.parse("now() > date('2020-01-01')").uses_time


def test_cycle_detection():
    fields = [
        {"key": "a", "calculated": True, "depends_on": ["b"]},
        {"key": "b", "calculated": True, "depends_on": ["a"]},
    ]
    with pytest.raises(ex.ExpressionError) as e:
        ex.calculation_order(fields)
    assert e.value.code == "cycle"


def test_time_dependency_propagates_through_fields():
    fields = [
        {"key": "start", "type": "date"},
        {"key": "age", "type": "number", "calculated": True, "expression": "days_between(start, today())", "depends_on": ["start"]},
        {"key": "old", "type": "text", "calculated": True, "expression": 'age > 30 ? "old" : "new"', "depends_on": ["age"]},
        {"key": "label", "type": "text", "calculated": True, "expression": '"x"', "depends_on": []},
    ]
    assert ex.time_dependent_keys(fields) == {"age", "old"}


def test_calculated_fields_compute_in_dependency_order():
    fields = [
        {"key": "price", "label": "Price", "type": "number", "config": {}},
        {"key": "qty", "label": "Qty", "type": "number", "config": {"integer_only": True}},
        {"key": "total", "label": "Total", "type": "number", "calculated": True, "expression": "subtotal * 1.1", "depends_on": ["subtotal"]},
        {"key": "subtotal", "label": "Subtotal", "type": "number", "calculated": True, "expression": "price * qty", "depends_on": ["price", "qty"]},
    ]
    out = validate_atom_values(fields, {"price": 2.5, "qty": 4})
    assert out["subtotal"] == 10
    assert out["total"] == pytest.approx(11)
    # a missing input leaves the calculated field empty
    assert "subtotal" not in validate_atom_values(fields, {"price": 2.5})


def test_result_type_mismatch():
    with pytest.raises(ex.ExpressionError) as e:
        ex.from_cel(ex.evaluate(ex.parse("1 > 0"), {}), "number")
    assert e.value.code == "type"
