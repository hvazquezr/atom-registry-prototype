import pytest

from tests.conftest import add_field, publish


def approve(reviewer, cr, expect=200):
    r = reviewer.post(f"/internal/change-requests/{cr['id']}/approve", json={})
    assert r.status_code == expect, r.text
    return r


def link(editor, reviewer, src, rtype, tgt, note="because", attributes=None):
    cr = editor.patch(f"/api/v1/atoms/{src['id']}", json={"relations": {"add": [
        {"type": rtype, "target_atom_id": tgt["id"], "note": note, "attributes": attributes or {}}]}})
    assert cr.status_code == 202, cr.text
    approve(reviewer, cr.json())


def make_form(editor, items, key="intake", sections=None):
    body = {"key": key, "name": "Intake", "sections": sections or [{"title": "Main", "items": items}]}
    r = editor.post("/api/v1/forms", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def item(atom, **kw):
    return {"atom_id": atom["id"], **kw}


def codes(form):
    return {(i["code"], i["severity"]) for i in form["issues"]}


def publish_form(editor, reviewer, form_id, notes="first"):
    cr = editor.post(f"/api/v1/forms/{form_id}/publish", json={"notes": notes})
    assert cr.status_code == 202, cr.text
    approve(reviewer, cr.json())
    return cr.json()


@pytest.fixture
def typed(admin):
    """Fields that say what kind of answer an atom takes."""
    add_field(admin, key="data_type", label="Data type", type="options",
              config={"choices": [{"value": v} for v in ("string", "integer", "decimal", "date", "boolean")]})
    add_field(admin, key="max_length", label="Max length", type="number", config={"integer_only": True, "min": 1})
    publish(admin)


def test_build_publish_and_pin(editor, reviewer, make_atom):
    first = make_atom({"name": "First name"})
    last = make_atom({"name": "Last name", "description": "Family name"})
    form = make_form(editor, [item(first), item(last, label="Surname", required=True)])
    assert [i["key"] for i in form["draft"]["sections"][0]["items"]] == ["first_name", "last_name"]
    resolved = form["resolved_draft"]["sections"][0]["items"]
    assert resolved[1]["label"] == "Surname" and resolved[1]["help_text"] == "Family name"
    assert form["issues"] == [] and form["can_publish"]

    # A read-only token can't create forms.
    tok = editor.post("/internal/tokens", json={"name": "ro", "scopes": ["forms:read"]}).json()["token"]
    ro = {"Authorization": f"Bearer {tok}"}
    assert editor.client.post("/api/v1/forms", json={"key": "x", "name": "X"}, headers=ro).status_code == 403
    assert editor.client.get("/api/v1/forms/intake", headers=ro).status_code == 200
    cr = editor.post(f"/api/v1/forms/{form['id']}/publish", json={"notes": "v1"}).json()
    assert cr["kind"] == "publish_form" and cr["form_name"] == "Intake"
    assert editor.post(f"/api/v1/forms/{form['id']}/publish", json={}).json()["error"]["code"] == "pending_request"
    detail = editor.get(f"/api/v1/change-requests/{cr['id']}").json()
    assert detail["form_preview"]["version"] == 1
    assert {a["key"] for a in detail["form_preview"]["diff"]["added"]} == {"first_name", "last_name"}
    approve(reviewer, cr)

    v1 = editor.get("/api/v1/forms/intake/versions/latest").json()
    assert v1["version"] == 1 and v1["notes"] == "v1"
    assert v1["sections"][0]["items"][0]["atom_revision"] == 1

    # Changing the atom doesn't change the published version, but it's reported.
    approve(reviewer, editor.patch(f"/api/v1/atoms/{first['id']}", json={"values": {"description": "Given name"}}).json())
    assert editor.get("/api/v1/forms/intake/versions/1").json()["sections"][0]["items"][0]["help_text"] == ""
    f = editor.get(f"/api/v1/forms/{form['id']}").json()
    assert [(u["key"], u["pinned_revision"], u["current_revision"]) for u in f["atoms_updated"]] == [("first_name", 1, 2)]
    assert f["draft_changes"]["changed"][0]["key"] == "first_name"

    publish_form(editor, reviewer, form["id"], notes="pick up atom change")
    v2 = editor.get("/api/v1/forms/intake/versions/latest").json()
    assert v2["version"] == 2 and v2["sections"][0]["items"][0]["help_text"] == "Given name"
    assert editor.get(f"/api/v1/forms/{form['id']}").json()["atoms_updated"] == []
    r = editor.post(f"/api/v1/forms/{form['id']}/publish", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_changes"
    assert [v["version"] for v in editor.get("/api/v1/forms/intake/versions").json()] == [2, 1]


def test_draft_edits_and_conflicts(editor, reviewer, make_atom):
    a, b = make_atom({"name": "A"}), make_atom({"name": "B"})
    form = make_form(editor, [item(a)])
    r = editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"items": [item(a), item(a)]}]})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "duplicate_atom"
    r = editor.put(f"/api/v1/forms/{form['id']}/draft",
                   json={"base_revision": 1, "sections": [{"title": "One", "items": [item(b, key="bee")]}, {"title": "Two", "items": [item(a)]}]})
    assert r.status_code == 200 and r.headers["ETag"] == '"2"'
    assert [s["title"] for s in r.json()["draft"]["sections"]] == ["One", "Two"]
    r = editor.put(f"/api/v1/forms/{form['id']}/draft", headers={"If-Match": '"1"'}, json={"name": "Old"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale"

    # Editing the draft after submitting makes the request stale until the submitter updates it.
    cr = editor.post(f"/api/v1/forms/{form['id']}/publish", json={}).json()
    editor.put(f"/api/v1/forms/{form['id']}/draft", json={"name": "Renamed"})
    assert editor.get(f"/api/v1/change-requests/{cr['id']}").json()["stale"]
    assert reviewer.post(f"/internal/change-requests/{cr['id']}/approve", json={}).json()["error"]["code"] == "stale"
    assert editor.patch(f"/api/v1/change-requests/{cr['id']}", json={}).status_code == 200
    approve(reviewer, cr)
    assert editor.get("/api/v1/forms/intake/versions/1").json()["form"]["name"] == "Renamed"


def test_relations_shape_the_form(editor, reviewer, make_atom):
    full, first, middle = make_atom({"name": "Full name"}), make_atom({"name": "First name"}), make_atom({"name": "Middle name"})
    link(editor, reviewer, full, "dependency", first)
    link(editor, reviewer, full, "includes", middle)
    form = make_form(editor, [item(full)])
    assert codes(form) == {("missing_dependency", "error"), ("missing_included", "warning")}
    dep = next(i for i in form["issues"] if i["code"] == "missing_dependency")
    assert dep["add_atom_id"] == first["id"] and dep["add_atom_name"] == "First name"
    assert not form["can_publish"]
    r = editor.post(f"/api/v1/forms/{form['id']}/publish", json={})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "missing_dependency"

    f = editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"items": [item(first), item(full), item(middle)]}]}).json()
    assert f["issues"] == []
    items = {i["key"]: i for i in f["resolved_draft"]["sections"][0]["items"]}
    assert items["middle_name"]["parent"] == "full_name"
    assert items["full_name"]["depends_on"] == ["first_name"]


def test_required_if_conditions(typed, editor, reviewer, make_atom):
    has = make_atom({"name": "Has middle name", "data_type": "boolean"})
    middle = make_atom({"name": "Middle name", "data_type": "string", "max_length": 5})
    email = make_atom({"name": "Email"})
    phone = make_atom({"name": "Phone"})
    link(editor, reviewer, middle, "required_if", has, attributes={"condition": "value == true"})
    link(editor, reviewer, phone, "required_if", email)  # no condition: "when answered"

    form = make_form(editor, [item(has), item(middle), item(email), item(phone)])
    assert form["issues"] == []
    publish_form(editor, reviewer, form["id"])
    defn = editor.get("/api/v1/forms/intake/versions/1").json()
    m = defn["sections"][0]["items"][1]
    assert m["conditions"][0]["when"] == "has_middle_name" and m["conditions"][0]["condition"] == "value == true"
    assert m["input"] == {"type": "text", "answer_type": "text", "config": {"max_length": 5}}
    assert defn["sections"][0]["items"][0]["input"]["type"] == "boolean"

    def check(answers):
        return editor.post("/api/v1/forms/intake/validate", json={"answers": answers}).json()

    r = check({"has_middle_name": False, "middle_name": "Q"})
    vis = {f["key"]: (f["visible"], f["required"]) for f in r["fields"]}
    assert vis["middle_name"] == (False, False) and r["valid"] and r["ignored"] == ["middle_name"]
    r = check({"has_middle_name": True})
    assert not r["valid"] and {(e["field"], e["code"]) for e in r["errors"]} == {("middle_name", "required")}
    r = check({"has_middle_name": "yes", "middle_name": "Quincy"})
    assert r["errors"][0]["code"] == "invalid" and "at most 5" in r["errors"][0]["message"]
    r = check({"has_middle_name": True, "middle_name": "Q", "email": "a@b.c"})
    assert {e["field"] for e in r["errors"]} == {"phone"}
    r = check({"email": "a@b.c", "phone": "555", "nope": 1})
    assert [e["code"] for e in r["errors"]] == ["unknown_field"]


def test_required_if_rules_at_publish(editor, reviewer, make_atom):
    a, b, c = make_atom({"name": "A"}), make_atom({"name": "B"}), make_atom({"name": "C"})
    link(editor, reviewer, a, "required_if", b)
    form = make_form(editor, [item(a)])
    assert ("missing_trigger", "error") in codes(form)
    f = editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"items": [item(a, required=True), item(b)]}]}).json()
    assert codes(f) == {("required_conditional", "error"), ("trigger_after", "warning")}
    # A loop of Required if links means neither question can ever be shown.
    link(editor, reviewer, b, "required_if", a)
    f = editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"items": [item(a), item(b), item(c)]}]}).json()
    assert {i["item"] for i in f["issues"] if i["code"] == "never_shown"} == {"a", "b"}


def test_conditions_are_checked_on_links(editor, make_atom):
    a, b = make_atom({"name": "A"}), make_atom({"name": "B"})
    for cond, code in (("other == 1", "invalid_condition"), ("value ==", "invalid_condition")):
        r = editor.patch(f"/api/v1/atoms/{a['id']}", json={"relations": {"add": [
            {"type": "required_if", "target_atom_id": b["id"], "note": "x", "attributes": {"condition": cond}}]}})
        assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == code


def test_input_types_and_settings(typed, admin, editor, reviewer, make_atom):
    age = make_atom({"name": "Age", "data_type": "integer"})
    born = make_atom({"name": "Born", "data_type": "date"})
    form = make_form(editor, [item(age), item(born)])
    publish_form(editor, reviewer, form["id"])

    def check(answers):
        return editor.post("/api/v1/forms/intake/validate", json={"answers": answers}).json()

    r = check({"age": 3.5, "born": "nope"})
    assert {e["field"] for e in r["errors"]} == {"age", "born"}
    r = check({"age": "41", "born": "1985-02-03"})
    assert r["valid"] and r["answers"] == {"age": 41, "born": "1985-02-03"}

    # Admins choose which atom fields drive the inputs.
    assert editor.put("/api/v1/form-input-settings", json={"default_type": "number"}).status_code == 403
    r = admin.put("/api/v1/form-input-settings", json={"type_map": {"integer": "bogus"}})
    assert r.status_code == 422
    r = admin.put("/api/v1/form-input-settings", json={"type_field": None, "default_type": "boolean"})
    assert r.status_code == 200
    d = editor.get("/api/v1/forms/intake/versions/draft").json()
    assert {i["input"]["type"] for s in d["sections"] for i in s["items"]} == {"boolean"}
    # Published versions keep the inputs they were published with.
    assert editor.get("/api/v1/forms/intake/versions/1").json()["sections"][0]["items"][0]["input"]["answer_type"] == "integer"


def test_deprecated_atoms_and_forms(editor, reviewer, make_atom):
    a, b = make_atom({"name": "A"}), make_atom({"name": "B"})
    form = make_form(editor, [item(a), item(b)])
    approve(reviewer, editor.post(f"/api/v1/atoms/{a['id']}/deprecate", json={}).json())
    assert ("atom_deprecated", "error") in codes(editor.get(f"/api/v1/forms/{form['id']}").json())
    assert editor.get("/api/v1/forms", params={"atom_id": a["id"]}).json()["total"] == 1

    approve(reviewer, editor.post(f"/api/v1/forms/{form['id']}/deprecate", json={"comment": "old"}).json())
    assert editor.get("/api/v1/forms").json()["total"] == 0
    assert editor.get("/api/v1/forms", params={"status": "all"}).json()["items"][0]["status"] == "deprecated"
    assert editor.put(f"/api/v1/forms/{form['id']}/draft", json={"name": "x"}).json()["error"]["code"] == "deprecated"
    reqs = editor.get(f"/api/v1/forms/{form['id']}/change-requests").json()
    assert [r["kind"] for r in reqs] == ["deprecate_form"]
