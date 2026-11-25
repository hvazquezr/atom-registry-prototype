from tests.conftest import add_field, publish


def test_create_field_and_publish_schema(admin):
    add_field(admin, key="size", label="Size", type="number", config={"min": 0})
    draft = admin.get("/internal/schema/draft").json()
    assert draft["changes"]["added"] == ["size"]
    v = publish(admin)
    assert v["version"] == 2
    schema = admin.get("/api/v1/schema").json()
    assert [f["key"] for f in schema["fields"]] == ["name", "description", "formula", "size"]
    assert {r["key"] for r in schema["relation_types"]} == {"dependency", "includes", "required_if", "calculated_from"}
    r = admin.post("/internal/schema/publish", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_changes"


def test_invalid_definitions_are_rejected(admin):
    r = admin.post("/internal/fields", json={"key": "Bad Key", "label": "", "type": "text", "config": {"regex": "("}})
    assert r.status_code == 422
    assert {e["field"] for e in r.json()["error"]["details"]} == {"key", "label", "config"}
    r = admin.post("/internal/fields", json={"key": "kind", "label": "Kind", "type": "options", "config": {}})
    assert r.status_code == 422
    r = admin.post("/internal/fields", json={"key": "in", "label": "In", "type": "text"})
    assert r.json()["error"]["details"][0]["code"] == "reserved"
    r = admin.post("/internal/fields", json={"key": "n", "label": "N", "type": "number", "config": {"min": 5, "max": 1}})
    assert r.status_code == 422


def test_expression_checks(admin):
    add_field(admin, key="price", label="Price", type="number")
    ok = admin.post("/internal/fields/check-expression",
                    json={"expression": "price * 2", "type": "number", "values": {"price": 4}}).json()
    assert ok["ok"] and ok["depends_on"] == ["price"] and ok["result"] == 8
    bad = admin.post("/internal/fields/check-expression", json={"expression": "missing + 1", "type": "number"}).json()
    assert not bad["ok"] and bad["error"]["code"] == "unknown_field"
    wrong_type = admin.post("/internal/fields/check-expression", json={"expression": "price > 1", "type": "number"}).json()
    assert not wrong_type["ok"] and wrong_type["error"]["code"] == "type"
    td = admin.post("/internal/fields/check-expression", json={"expression": "today()", "type": "date"}).json()
    assert td["ok"] and td["time_dependent"]


def test_cycles_and_self_references_are_rejected(admin):
    add_field(admin, key="a", label="A", type="number")
    b = add_field(admin, key="b", label="B", type="number", calculated=True, expression="a + 1")
    r = admin.post("/internal/fields", json={"key": "c", "label": "C", "type": "number", "calculated": True, "expression": "c + 1"})
    assert r.json()["error"]["details"][0]["code"] == "self_reference"
    add_field(admin, key="c", label="C", type="number", calculated=True, expression="b + 1")
    r = admin.patch(f"/internal/fields/{b['id']}", json={"expression": "c + 1"})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "cycle"


def test_field_in_use_cannot_be_deprecated_and_keys_freeze_after_publish(admin):
    a = add_field(admin, key="a", label="A", type="number")
    add_field(admin, key="b", label="B", type="number", calculated=True, expression="a * 2")
    r = admin.post(f"/internal/fields/{a['id']}/deprecate")
    assert r.status_code == 409 and r.json()["error"]["code"] == "in_use"
    publish(admin)
    r = admin.patch(f"/internal/fields/{a['id']}", json={"key": "renamed"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "key_frozen"
    assert admin.delete(f"/internal/fields/{a['id']}").status_code == 409


def test_changing_a_type_that_breaks_another_expression(admin):
    a = add_field(admin, key="a", label="A", type="number")
    add_field(admin, key="b", label="B", type="number", calculated=True, expression="a * 2")
    r = admin.patch(f"/internal/fields/{a['id']}", json={"type": "text"})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "would_break"


def test_publishing_a_new_expression_recalculates_atoms(admin, make_atom):
    add_field(admin, key="qty", label="Qty", type="number")
    total = add_field(admin, key="total", label="Total", type="number", calculated=True, expression="qty * 2")
    publish(admin)
    atom = make_atom({"name": "Widget", "qty": 3})
    assert atom["values"]["total"] == 6
    admin.patch(f"/internal/fields/{total['id']}", json={"expression": "qty * 10"})
    assert publish(admin)["recalculating"] == ["total"]
    after = admin.get(f"/api/v1/atoms/{atom['id']}").json()
    assert after["values"]["total"] == 30 and after["revision"] == 2 and after["schema_version"] == 3


def test_expressions_refer_to_fields_with_at(admin, make_atom):
    add_field(admin, key="qty", label="Qty", type="number")
    # The @ form, like atom formulas; "@" inside a string is just text.
    total = add_field(admin, key="total", label="Total", type="number", calculated=True, expression="@qty * 2")
    assert total["depends_on"] == ["qty"]
    label = add_field(admin, key="label2", label="Label", type="text", calculated=True, expression='@name + "@x"')
    assert label["depends_on"] == ["name"]
    ok = admin.post("/internal/fields/check-expression",
                    json={"expression": "@qty + 1", "type": "number", "values": {"qty": 4}}).json()
    assert ok["ok"] and ok["result"] == 5
    bad = admin.post("/internal/fields/check-expression", json={"expression": "@ + 1", "type": "number"}).json()
    assert not bad["ok"] and "After @" in bad["error"]["message"]

    # Bare names still work and are saved in the @ form.
    r = admin.patch(f"/internal/fields/{total['id']}", json={"expression": "qty * 3 + size(name)"})
    assert r.status_code == 200 and r.json()["expression"] == "@qty * 3 + size(@name)"
    publish(admin)
    atom = make_atom({"name": "Box", "qty": 2})
    assert atom["values"]["total"] == 9 and atom["values"]["label2"] == "Box@x"


def test_old_bare_expressions_still_count_as_published(admin, db):
    from app.models import SchemaVersion

    add_field(admin, key="qty", label="Qty", type="number")
    add_field(admin, key="total", label="Total", type="number", calculated=True, expression="qty * 2")
    publish(admin)
    # A schema published before the @ form has the bare expression.
    sv = db.query(SchemaVersion).order_by(SchemaVersion.version.desc()).first()
    sv.fields = [f | {"expression": "qty * 2"} if f["key"] == "total" else f for f in sv.fields]
    db.commit()
    assert admin.get("/internal/schema/draft").json()["changes"]["has_changes"] is False
