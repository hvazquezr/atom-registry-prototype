import datetime as dt

import pytest

from app.services import formulas
from tests.test_forms import (
    approve,
    item,
    make_form,
    publish_form,
    typed,  # noqa: F401  (fixture)
)

AGE = ("today().getFullYear() - @date_of_birth.getFullYear() - (today().getMonth() < @date_of_birth.getMonth() || "
       "(today().getMonth() == @date_of_birth.getMonth() && today().getDayOfMonth() < @date_of_birth.getDayOfMonth()) ? 1 : 0)")


def test_translate_skips_strings_and_collects_refs():
    cel, refs = formulas.translate('@a_b + "x@y" + @c + @a_b')
    assert cel == 'a_b + "x@y" + c + a_b' and refs == ["a_b", "c"]
    assert formulas.ref_name("Date of birth") == "date_of_birth"
    assert formulas.ref_name("3D score") == "a_3d_score"
    for bad, msg in (("@ + 1", "After @"), ("1 + 2", "at least one"), ("@a + b", "@b"), ("@a +", "Syntax")):
        with pytest.raises(formulas.FormulaError, match=msg):
            formulas.parse(bad)


def submit(editor, values):
    return editor.post("/api/v1/atoms", json={"values": values})


def calc_links(editor, atom_id):
    rels = editor.get(f"/api/v1/atoms/{atom_id}/relations").json()
    rels = rels.get("items", rels) if isinstance(rels, dict) else rels
    return sorted((r["attributes"]["ref"], r["other_atom_id"]) for r in rels if r["type"] == "calculated_from" and r["direction"] == "out")


def test_formula_binds_links_and_survives_renames(editor, reviewer, make_atom):
    weight = make_atom({"name": "Weight"})
    height = make_atom({"name": "Height"})
    bmi = make_atom({"name": "BMI", "formula": "@weight / (@height * @height)"})
    assert calc_links(editor, bmi["id"]) == sorted([("weight", weight["id"]), ("height", height["id"])])

    # Renaming an input keeps the formula working: the name stays bound by its link.
    cr = editor.patch(f"/api/v1/atoms/{height['id']}", json={"values": {"name": "Body height"}}).json()
    approve(reviewer, cr)
    cr = editor.patch(f"/api/v1/atoms/{bmi['id']}", json={"values": {"description": "Body mass index"}})
    assert cr.status_code == 202, cr.text
    approve(reviewer, cr.json())
    assert calc_links(editor, bmi["id"]) == sorted([("weight", weight["id"]), ("height", height["id"])])

    # Dropping an input from the formula removes its link.
    cr = editor.patch(f"/api/v1/atoms/{bmi['id']}", json={"values": {"formula": "@weight * 2"}}).json()
    approve(reviewer, cr)
    assert calc_links(editor, bmi["id"]) == [("weight", weight["id"])]

    # The links can't be edited by hand.
    r = editor.patch(f"/api/v1/atoms/{bmi['id']}", json={"relations": {"add": [
        {"type": "calculated_from", "target_atom_id": height["id"]}]}})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "managed"


def test_formula_problems_are_reported(editor, reviewer, make_atom):
    a = make_atom({"name": "Alpha"})
    make_atom({"name": "Twin"})
    make_atom({"name": "twin"})

    def problems(values, atom=None):
        r = editor.patch(f"/api/v1/atoms/{atom['id']}", json={"values": values}) if atom else submit(editor, values)
        assert r.status_code == 422, r.text
        return " ".join(e["message"] for e in r.json()["error"]["details"])

    assert "No active atom goes by @nobody" in problems({"name": "X", "formula": "@nobody + 1"})
    assert "More than one atom goes by @twin" in problems({"name": "X", "formula": "@twin + 1"})
    assert "Put @ in front" in problems({"name": "X", "formula": "@alpha + beta"})
    assert "this atom itself" in problems({"formula": "@alpha + 1"}, a)

    # Loops: Beta from Alpha, then Alpha from Beta.
    make_atom({"name": "Beta", "formula": "@alpha + 1"})
    assert "loop" in problems({"formula": "@beta + 1"}, a)


def test_calculated_answers_on_a_form(typed, editor, reviewer, make_atom):  # noqa: F811
    dob = make_atom({"name": "Date of birth", "data_type": "date"})
    age = make_atom({"name": "Age", "data_type": "integer", "formula": AGE})
    other = make_atom({"name": "Nickname"})

    # Age needs Date of birth on the form.
    form = make_form(editor, [item(age), item(other)])
    assert ("missing_input", "error") in {(i["code"], i["severity"]) for i in form["issues"]}
    assert not form["can_publish"]

    r = editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"title": "Main", "items": [
        item(dob), item(age, required=True), item(other)]}]})
    assert r.status_code == 200, r.text
    assert ("required_calculated", "error") in {(i["code"], i["severity"]) for i in r.json()["issues"]}

    r = editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"title": "Main", "items": [
        item(dob), item(age), item(other)]}]}).json()
    assert r["issues"] == [], r["issues"]
    calc = r["resolved_draft"]["sections"][0]["items"][1]["calculation"]
    assert calc["inputs"] == [{"ref": "date_of_birth", "key": "date_of_birth", "atom_id": dob["id"], "atom_name": "Date of birth"}]
    publish_form(editor, reviewer, form["id"])

    def check(answers):
        return editor.post("/api/v1/forms/intake/validate", json={"answers": answers}).json()

    today = dt.date.today()
    born = today.replace(year=today.year - 30)
    r = check({"date_of_birth": born.isoformat(), "age": 99})
    assert r["valid"], r
    assert r["answers"]["age"] == 30 and r["ignored"] == ["age"]
    assert {"key": "age", "visible": True, "required": False, "calculated": True, "value": 30} in r["fields"]

    r = check({})
    assert r["valid"] and "age" not in r["answers"]


def test_calculations_chain_and_report_failures(editor, reviewer, make_atom):
    w = make_atom({"name": "Weight"})
    h = make_atom({"name": "Height"})
    bmi = make_atom({"name": "BMI", "formula": "double(@weight) / (double(@height) * double(@height))"})
    heavy = make_atom({"name": "Heavy", "formula": 'double(@bmi) > 25.0 ? "yes" : "no"'})
    form = make_form(editor, [item(heavy), item(bmi), item(w), item(h)], key="body")
    assert not [i for i in form["issues"] if i["severity"] == "error"], form["issues"]
    publish_form(editor, reviewer, form["id"])

    r = editor.post("/api/v1/forms/body/validate", json={"answers": {"weight": "90", "height": "2"}}).json()
    assert r["answers"]["bmi"] == "22.5" and r["answers"]["heavy"] == "no", r

    r = editor.post("/api/v1/forms/body/validate", json={"answers": {"weight": "heavy", "height": "2"}}).json()
    assert not r["valid"] and r["errors"][0]["code"] == "calculation_failed"
