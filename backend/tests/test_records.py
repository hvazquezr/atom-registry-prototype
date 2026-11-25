import datetime as dt

import pytest

from tests.test_forms import (
    item,
    link,
    make_form,
    publish_form,
    typed,  # noqa: F401  (fixture)
)
from tests.test_formulas import AGE


@pytest.fixture
def person(typed, editor, reviewer, make_atom):  # noqa: F811
    """A published Person form: names, a Required if pair, a number and a calculated age."""
    first = make_atom({"name": "First name", "data_type": "string", "max_length": 20})
    last = make_atom({"name": "Last name", "data_type": "string"})
    has_mid = make_atom({"name": "Has middle name", "data_type": "boolean"})
    mid = make_atom({"name": "Middle name", "data_type": "string"})
    link(editor, reviewer, mid, "required_if", has_mid, attributes={"condition": "value == true"})
    income = make_atom({"name": "Income", "data_type": "decimal"})
    dob = make_atom({"name": "Date of birth", "data_type": "date"})
    age = make_atom({"name": "Age", "data_type": "integer", "formula": AGE})
    form = make_form(editor, [item(first, required=True), item(last, required=True), item(has_mid), item(mid),
                              item(income), item(dob), item(age)], key="person")
    assert not [i for i in form["issues"] if i["severity"] == "error"], form["issues"]
    publish_form(editor, reviewer, form["id"])
    return {"form": form, "atoms": {"first": first, "last": last, "income": income}}


def test_save_partial_then_complete(person, records):
    born = dt.date.today().replace(year=dt.date.today().year - 40)
    r = records.post("/api/v1/records", json={"form": "person", "answers": {
        "first_name": "Ada", "date_of_birth": born.isoformat(), "age": 1, "middle_name": "Hidden"}})
    assert r.status_code == 201, r.text
    rec = r.json()
    assert r.headers["ETag"] == '"1"'
    assert rec["form"]["key"] == "person" and rec["form_version"] == 1
    # Calculated on save (what was sent for it is ignored), hidden answers dropped, required ones missing.
    assert rec["answers"] == {"first_name": "Ada", "date_of_birth": born.isoformat(), "age": 40}
    assert rec["missing"] == ["last_name"] and not rec["complete"]
    assert rec["title"] == "Ada"
    assert {"key": "middle_name", "visible": False, "required": False} in rec["fields"]

    # Bad answers block the save; nothing is stored.
    r = records.patch(f"/api/v1/records/{rec['id']}", json={"answers": {"first_name": "x" * 30, "income": "lots", "nope": 1}})
    assert r.status_code == 422
    assert {e["field"] for e in r.json()["error"]["details"]} == {"first_name", "income", "nope"}

    # Required if: saying yes makes the middle name required.
    r = records.patch(f"/api/v1/records/{rec['id']}", headers={"If-Match": '"1"'},
                      json={"answers": {"last_name": "Lovelace", "has_middle_name": True}})
    assert r.status_code == 200, r.text
    assert r.json()["missing"] == ["middle_name"] and r.json()["revision"] == 2

    r = records.patch(f"/api/v1/records/{rec['id']}", json={"answers": {"middle_name": "King", "income": "1200.50"}})
    body = r.json()
    assert body["complete"] and body["missing"] == [] and body["answers"]["income"] == 1200.5

    # Stale edits are refused.
    r = records.patch(f"/api/v1/records/{rec['id']}", headers={"If-Match": '"1"'}, json={"answers": {"income": 1}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_revision"

    # Clearing an answer with null.
    r = records.patch(f"/api/v1/records/{rec['id']}", json={"answers": {"has_middle_name": None}})
    assert "middle_name" not in r.json()["answers"] and r.json()["complete"]

    # Saving the same answers doesn't add a revision.
    before = r.json()["revision"]
    r = records.patch(f"/api/v1/records/{rec['id']}", json={"answers": {"first_name": "Ada"}})
    assert r.json()["revision"] == before

    history = records.get(f"/api/v1/records/{rec['id']}/revisions").json()
    assert [h["revision"] for h in history] == [4, 3, 2, 1]
    assert history[-1]["summary"] == "Created"
    assert history[-2]["summary"] == "Changed Last name, Has middle name"


def test_search_filters_and_archive(person, records):
    def make(answers):
        r = records.post("/api/v1/records", json={"form": "person", "answers": answers})
        assert r.status_code == 201, r.text
        return r.json()

    ada = make({"first_name": "Ada", "last_name": "Lovelace", "income": 500})
    alan = make({"first_name": "Alan", "last_name": "Turing", "income": 1500, "has_middle_name": True, "middle_name": "Mathison"})
    make({"first_name": "Grace"})

    def ids(**params):
        r = records.get("/api/v1/records", params={"form": "person", **params})
        assert r.status_code == 200, r.text
        return {x["id"] for x in r.json()["items"]}

    assert len(ids()) == 3
    assert ids(q="turing") == {alan["id"]}
    assert ids(**{"filter[income][gte]": "1000"}) == {alan["id"]}
    assert ids(**{"filter[last_name]": "Lovelace"}) == {ada["id"]}
    assert ids(**{"filter[has_middle_name]": "true"}) == {alan["id"]}
    assert ids(**{"filter[last_name][exists]": "false"}) == ids(complete="false")
    r = records.get("/api/v1/records", params={"form": "person", "sort": "-income"}).json()
    assert [x["id"] for x in r["items"]][:2] == [alan["id"], ada["id"]]
    assert records.get("/api/v1/records", params={"form": "person", "filter[nope]": "1"}).status_code == 400
    assert records.get("/api/v1/records", params={"filter[income]": "1"}).status_code == 400

    # Archived records leave the default list, can't be edited, and can be restored.
    r = records.post(f"/api/v1/records/{ada['id']}/archive", json={})
    assert r.status_code == 200 and r.json()["status"] == "archived"
    assert ada["id"] not in ids()
    assert ids(status="archived") == {ada["id"]}
    assert records.patch(f"/api/v1/records/{ada['id']}", json={"answers": {"income": 1}}).json()["error"]["code"] == "record_archived"
    assert records.post(f"/api/v1/records/{ada['id']}/restore", json={}).json()["status"] == "active"


def test_new_form_version_and_moving_records(person, editor, reviewer, records, make_atom):
    rec = records.post("/api/v1/records", json={"form": "person", "answers": {
        "first_name": "Ada", "last_name": "Lovelace", "income": 10}}).json()

    # Version 2 drops Income and adds a required Email.
    form = person["form"]
    email = make_atom({"name": "Email", "data_type": "string"})
    draft = editor.get(f"/api/v1/forms/{form['id']}").json()["draft"]
    items = [i for i in draft["sections"][0]["items"] if i["key"] != "income"] + [{"atom_id": email["id"], "required": True}]
    r = editor.put(f"/api/v1/forms/{form['id']}/draft", json={"sections": [{"title": "Main", "items": items}]})
    assert r.status_code == 200, r.text
    publish_form(editor, reviewer, form["id"], notes="v2")

    got = records.get(f"/api/v1/records/{rec['id']}").json()
    assert got["form_version"] == 1 and got["latest_form_version"] == 2 and got["complete"]

    # New records use the latest version.
    assert records.post("/api/v1/records", json={"form": "person", "answers": {}}).json()["form_version"] == 2

    r = records.patch(f"/api/v1/records/{rec['id']}", json={"form_version": "latest"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["form_version"] == 2 and body["dropped"] == ["income"]
    assert body["missing"] == ["email"] and "income" not in body["answers"]
    assert records.get(f"/api/v1/records/{rec['id']}/revisions").json()[0]["summary"] == "Moved to version 2"


def test_roles_and_form_rules(person, editor, reviewer, records, admin, client, make_atom):
    # Atom editors and reviewers can't see records; record managers can't change atoms or forms.
    assert editor.get("/api/v1/records").status_code == 403
    assert reviewer.get("/api/v1/records").status_code == 403
    assert editor.post("/api/v1/records", json={"form": "person", "answers": {}}).status_code == 403
    assert records.post("/api/v1/atoms", json={"values": {"name": "X"}}).status_code == 403
    assert records.post("/api/v1/forms", json={"key": "x", "name": "X"}).status_code == 403
    assert records.get("/api/v1/forms/person").status_code == 200

    # Viewers read records but can't write them.
    r = admin.post("/internal/users", json={"username": "vera", "password": "test-password", "roles": ["viewer"]})
    assert r.status_code == 201, r.text
    from tests.conftest import Api

    viewer = Api(client, "vera")
    rec = records.post("/api/v1/records", json={"form": "person", "answers": {"first_name": "Ada"}}).json()
    assert viewer.get(f"/api/v1/records/{rec['id']}").status_code == 200
    assert viewer.patch(f"/api/v1/records/{rec['id']}", json={"answers": {}}).status_code == 403
    assert admin.patch(f"/api/v1/records/{rec['id']}", json={"answers": {"last_name": "L"}}).status_code == 200

    # Unpublished forms can't hold records.
    draft_only = make_form(editor, [item(make_atom({"name": "Thing"}))], key="draft_only")
    r = records.post("/api/v1/records", json={"form": draft_only["key"], "answers": {}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "form_not_published"


def test_form_scoped_routes(person, editor, records, make_atom):
    base = "/api/v1/forms/person/records"
    r = records.post(base, json={"answers": {"first_name": "Ada", "income": 900}})
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["form"]["key"] == "person" and r.headers["Location"] == f"/api/v1/records/{rec['id']}"
    assert records.get(base, params={"filter[income][gt]": "500"}).json()["total"] == 1
    assert records.get(f"{base}/{rec['id']}").json()["id"] == rec["id"]
    r = records.patch(f"{base}/{rec['id']}", headers={"If-Match": '"1"'}, json={"answers": {"last_name": "Lovelace"}})
    assert r.status_code == 200 and r.json()["complete"]
    assert len(records.get(f"{base}/{rec['id']}/revisions").json()) == 2
    assert records.post(f"{base}/{rec['id']}/archive", json={}).json()["status"] == "archived"
    assert records.post(f"{base}/{rec['id']}/restore", json={}).json()["status"] == "active"

    # A record is only found under its own form.
    other = make_form(editor, [item(make_atom({"name": "Thing"}))], key="other")
    assert records.get(f"/api/v1/forms/{other['key']}/records/{rec['id']}").status_code == 404
    assert records.get("/api/v1/forms/nope/records").status_code == 404
