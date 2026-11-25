from tests.conftest import add_field, publish
from tests.test_forms import approve, item, make_form, publish_form, typed  # noqa: F401


def field_id(admin, key):
    return next(f["id"] for f in admin.get("/internal/fields").json() if f["key"] == key)


def set_key(admin, key, on=True):
    r = admin.patch(f"/internal/fields/{field_id(admin, key)}", json={"natural_key": on})
    assert r.status_code == 200, r.text
    return r.json()


# --- Atoms ------------------------------------------------------------------------------


def test_atom_lookup_by_single_key(admin, editor, reviewer, make_atom):
    add_field(admin, key="code", label="Code", type="text")
    publish(admin)
    a = make_atom({"name": "Alpha", "code": "AB-1"})
    make_atom({"name": "Beta", "code": "ab-2"})

    # No key yet.
    r = editor.get("/api/v1/atoms/key/AB-1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_natural_key"

    f = set_key(admin, "code")
    assert f["natural_key"] and f["required"]
    publish(admin)
    schema = editor.get("/api/v1/schema").json()
    assert schema["natural_key"] == ["code"]

    # Case and surrounding spaces don't matter.
    for url in ("/api/v1/atoms/key/AB-1", "/api/v1/atoms/key/ ab-1 ", "/api/v1/atoms/key?code=ab-1"):
        r = editor.get(url)
        assert r.status_code == 200, (url, r.text)
        assert r.json()["id"] == a["id"] and r.json()["natural_key"] == {"code": "AB-1"}
        assert r.headers["ETag"] == '"1"'
    assert editor.get("/api/v1/atoms/key/nope").status_code == 404

    # Duplicates are refused when submitting, whatever the case.
    r = editor.post("/api/v1/atoms", json={"values": {"name": "Gamma", "code": "Ab-1"}})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "duplicate_key"
    # The key is required.
    r = editor.post("/api/v1/atoms", json={"values": {"name": "Gamma"}})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "code"

    # Two pending requests for the same key: the second approval is refused.
    c1 = editor.post("/api/v1/atoms", json={"values": {"name": "C1", "code": "C"}}).json()
    c2 = editor.post("/api/v1/atoms", json={"values": {"name": "C2", "code": "c"}}).json()
    approve(reviewer, c1)
    approve(reviewer, c2, expect=422)

    # Changing an atom's own key is fine; to another atom's key is not.
    r = editor.patch(f"/api/v1/atoms/{a['id']}", json={"values": {"code": "ab-2"}})
    assert r.status_code == 422
    approve(reviewer, editor.patch(f"/api/v1/atoms/{a['id']}", json={"values": {"code": "AB-9"}}).json())
    assert editor.get("/api/v1/atoms/key/ab-9").json()["id"] == a["id"]

    # Deprecated atoms keep their key, so it can't be reused.
    approve(reviewer, editor.post(f"/api/v1/atoms/{a['id']}/deprecate", json={}).json())
    assert editor.get("/api/v1/atoms/key/AB-9").json()["status"] == "deprecated"
    r = editor.post("/api/v1/atoms", json={"values": {"name": "New", "code": "AB-9"}})
    assert r.status_code == 422


def test_atom_combined_key_and_publish_checks(admin, editor, reviewer, make_atom):
    add_field(admin, key="code", label="Code", type="text")
    add_field(admin, key="year", label="Year", type="number", config={"integer_only": True})
    add_field(admin, key="tags", label="Tags", type="options", config={"multiple": True, "choices": [{"value": "x"}]})
    publish(admin)
    a = make_atom({"name": "A", "code": "X", "year": 2024})
    make_atom({"name": "B", "code": "X", "year": 2025})
    make_atom({"name": "C", "code": "X"})

    # Fields that can't be keys.
    r = admin.patch(f"/internal/fields/{field_id(admin, 'tags')}", json={"natural_key": True})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "natural_key"

    # Code alone clashes; refused at publish with the atoms listed, and nothing changes.
    set_key(admin, "code")
    r = admin.post("/internal/schema/publish", json={"notes": "key"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "natural_key_conflict"
    assert len(r.json()["error"]["details"]["duplicates"]) == 2

    # Code + year: C has no year.
    set_key(admin, "year")
    r = admin.post("/internal/schema/publish", json={"notes": "key"})
    assert r.status_code == 409 and [m["name"] for m in r.json()["error"]["details"]["missing"]] == ["C"]

    c = editor.get("/api/v1/atoms?q=C").json()["items"]
    c_id = next(x["id"] for x in c if x["values"]["name"] == "C")
    # Fill C's year with the key off for now (the draft key isn't published yet).
    approve(reviewer, editor.patch(f"/api/v1/atoms/{c_id}", json={"values": {"year": 2026}}).json())
    publish(admin)

    assert editor.get("/api/v1/schema").json()["natural_key"] == ["code", "year"]
    r = editor.get("/api/v1/atoms/key?code=x&year=2024.0")
    assert r.status_code == 200 and r.json()["id"] == a["id"]
    assert r.json()["natural_key"] == {"code": "X", "year": 2024}
    r = editor.get("/api/v1/atoms/key/X")
    assert r.status_code == 400 and r.json()["error"]["code"] == "combined_key"
    r = editor.get("/api/v1/atoms/key?code=X")
    assert r.status_code == 400 and r.json()["error"]["code"] == "missing_key_part"
    r = editor.get("/api/v1/atoms/key?code=X&year=soon")
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_key_part"

    # Turning the key off again.
    set_key(admin, "code", False)
    set_key(admin, "year", False)
    publish(admin)
    assert editor.get("/api/v1/atoms/key?code=X&year=2024").status_code == 409


# --- Records ----------------------------------------------------------------------------


def keyed_form(editor, reviewer, make_atom, key_items=("email",)):
    email = make_atom({"name": "Email", "data_type": "string"})
    branch = make_atom({"name": "Branch", "data_type": "integer"})
    note = make_atom({"name": "Note", "data_type": "string"})
    atoms = {"email": email, "branch": branch, "note": note}
    form = make_form(editor, [item(a, natural_key=k in key_items) for k, a in atoms.items()], key="member")
    assert not [i for i in form["issues"] if i["severity"] == "error"], form["issues"]
    publish_form(editor, reviewer, form["id"])
    return form, atoms


def test_record_key_lookup_patch_and_upsert(typed, editor, reviewer, records, make_atom):  # noqa: F811
    form, _ = keyed_form(editor, reviewer, make_atom)
    v1 = editor.get(f"/api/v1/forms/{form['id']}/versions/1").json()
    assert v1["natural_key"] == ["email"]
    email_item = next(i for s in v1["sections"] for i in s["items"] if i["key"] == "email")
    assert email_item["required"] and email_item["requirement"] == "required"

    # The key answer is required to save; other required answers still can wait.
    r = records.post("/api/v1/data/member", json={"answers": {"note": "hi"}})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "email"
    r = records.post("/api/v1/data/member", json={"answers": {"email": "Ana@Example.com", "note": "hi"}})
    assert r.status_code == 201, r.text
    ana = r.json()
    assert ana["natural_key"] == {"email": "Ana@Example.com"}

    r = records.post("/api/v1/data/member", json={"answers": {"email": " ana@example.COM"}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_key"
    assert r.json()["error"]["details"]["record_id"] == ana["id"]

    # GET by key, both URL shapes.
    for url in ("/api/v1/data/member/key/ana@example.com", "/api/v1/data/member/key?email=ANA@example.com"):
        r = records.get(url)
        assert r.status_code == 200 and r.json()["id"] == ana["id"], url
    assert records.get("/api/v1/data/member/key/nobody@example.com").status_code == 404

    # PATCH by key, including changing the key.
    r = records.patch("/api/v1/data/member/key/ana@example.com", headers={"If-Match": '"1"'},
                      json={"answers": {"email": "ana.diaz@example.com"}})
    assert r.status_code == 200, r.text
    assert records.get("/api/v1/data/member/key/ana.diaz@example.com").json()["id"] == ana["id"]
    assert records.get("/api/v1/data/member/key/ana@example.com").status_code == 404
    # Clearing the key answer is refused.
    r = records.patch(f"/api/v1/data/member/{ana['id']}", json={"answers": {"email": None}})
    assert r.status_code == 422

    # PUT creates when the key is new...
    r = records.put("/api/v1/data/member/key/bo@example.com", json={"answers": {"note": "new"}})
    assert r.status_code == 201, r.text
    bo = r.json()
    assert bo["created"] and bo["answers"]["email"] == "bo@example.com"
    assert r.headers["Location"] == f"/api/v1/data/member/{bo['id']}"
    # ...and updates when it exists.
    r = records.put("/api/v1/data/member/key/BO@example.com", json={"answers": {"note": "changed", "email": "bo@example.com"}})
    assert r.status_code == 200 and not r.json()["created"] and r.json()["id"] == bo["id"]
    assert r.json()["answers"]["note"] == "changed"
    # A different key in the body is refused.
    r = records.put("/api/v1/data/member/key/bo@example.com", json={"answers": {"email": "other@example.com"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "key_mismatch"

    # Archived records keep their key.
    records.post(f"/api/v1/data/member/{bo['id']}/archive", json={})
    assert records.get("/api/v1/data/member/key/bo@example.com").json()["status"] == "archived"
    r = records.post("/api/v1/data/member", json={"answers": {"email": "bo@example.com"}})
    assert r.status_code == 409


def test_record_combined_key_and_new_versions(typed, editor, reviewer, records, make_atom):  # noqa: F811
    form, atoms = keyed_form(editor, reviewer, make_atom, key_items=("email", "branch"))
    r = records.post("/api/v1/data/member", json={"answers": {"email": "a@x.com", "branch": 1}})
    assert r.status_code == 201
    a = r.json()
    assert records.post("/api/v1/data/member", json={"answers": {"email": "a@x.com", "branch": 2}}).status_code == 201
    r = records.get("/api/v1/data/member/key?email=A@x.com&branch=1.0")
    assert r.status_code == 200 and r.json()["id"] == a["id"]
    r = records.get("/api/v1/data/member/key/a@x.com")
    assert r.status_code == 400 and r.json()["error"]["code"] == "combined_key"
    r = records.put("/api/v1/data/member/key?email=c@x.com&branch=3", json={"answers": {}})
    assert r.status_code == 201 and r.json()["answers"] == {"email": "c@x.com", "branch": 3}

    # Making email alone the key would clash: refused when submitting the publish request.
    draft = [item(atoms["email"], natural_key=True), item(atoms["branch"]), item(atoms["note"])]
    r = editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"title": "Main", "items": draft}]})
    assert r.status_code == 200, r.text
    r = editor.post(f"/api/v1/forms/{form['id']}/publish", json={"notes": "email only"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "natural_key_conflict"

    # Note as the key: existing records have no note, so they lose their key until it's answered.
    draft = [item(atoms["email"]), item(atoms["branch"]), item(atoms["note"], natural_key=True)]
    editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"title": "Main", "items": draft}]})
    publish_form(editor, reviewer, form["id"], notes="note key")
    assert records.get("/api/v1/data/member/key?email=a@x.com&branch=1").status_code == 400
    assert records.get(f"/api/v1/data/member/{a['id']}").json()["natural_key"] is None
    # The record's own version has the note question, so editing it now needs the key answer.
    r = records.patch(f"/api/v1/data/member/{a['id']}", json={"answers": {"branch": 5}})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "note"
    r = records.patch(f"/api/v1/data/member/{a['id']}", json={"answers": {"note": "First"}})
    assert r.status_code == 200
    assert records.get("/api/v1/data/member/key/first").json()["id"] == a["id"]


def test_key_questions_that_are_not_allowed(typed, editor, reviewer, make_atom):  # noqa: F811
    yes = make_atom({"name": "Agrees", "data_type": "boolean"})
    form = make_form(editor, [item(yes, natural_key=True)], key="bad")
    assert ("natural_key_not_allowed", "error") in {(i["code"], i["severity"]) for i in form["issues"]}
    r = editor.post(f"/api/v1/forms/{form['id']}/publish", json={"notes": ""})
    assert r.status_code == 422
