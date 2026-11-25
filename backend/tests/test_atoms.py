from tests.conftest import add_field, publish


def setup_fields(admin):
    add_field(admin, key="code", label="Code", type="text", required=True, config={"regex": "[A-Z]{3}-[0-9]{3}"})
    add_field(admin, key="size", label="Size", type="number", config={"min": 1, "max": 1000})
    add_field(admin, key="since", label="Since", type="date")
    add_field(admin, key="tags", label="Tags", type="options",
              config={"multiple": True, "choices": [{"value": "pii", "label": "Personal data"}, {"value": "fin"}]})
    add_field(admin, key="age", label="Age", type="number", calculated=True, expression="days_between(since, today())")
    publish(admin)


def test_create_goes_through_review(admin, editor, reviewer):
    setup_fields(admin)
    r = editor.post("/api/v1/atoms", json={"values": {"name": "First name", "code": "PER-001"}, "comment": "new"})
    assert r.status_code == 202
    cr = r.json()
    assert cr["status"] == "pending" and cr["kind"] == "create"
    assert editor.get("/api/v1/atoms").json()["total"] == 0  # nothing live yet
    assert editor.post(f"/internal/change-requests/{cr['id']}/approve", json={}).status_code == 403
    queue = reviewer.get("/internal/review-queue").json()
    assert [q["atom_name"] for q in queue] == ["First name"]
    a = reviewer.post(f"/internal/change-requests/{cr['id']}/approve", json={"comment": "ok"})
    assert a.status_code == 200
    atom = a.json()["atom"]
    assert atom["revision"] == 1 and atom["values"]["code"] == "PER-001"
    assert editor.get("/api/v1/atoms").json()["total"] == 1


def test_invalid_values_are_rejected_at_submission(admin, editor):
    setup_fields(admin)
    r = editor.post("/api/v1/atoms", json={"values": {"name": "X", "code": "bad", "size": 0, "age": 3}})
    assert r.status_code == 422
    assert {e["field"] for e in r.json()["error"]["details"]} == {"code", "size", "age"}


def test_reviewers_cannot_approve_their_own_requests(admin, reviewer):
    setup_fields(admin)
    r = reviewer.post("/api/v1/atoms", json={"values": {"name": "Mine", "code": "ABC-001"}})
    a = reviewer.post(f"/internal/change-requests/{r.json()['id']}/approve", json={})
    assert a.status_code == 403 and a.json()["error"]["code"] == "self_review"
    assert reviewer.get("/internal/review-queue").json()[0]["can_review"] is False


def test_admins_can_approve_their_own_requests(admin):
    setup_fields(admin)
    r = admin.post("/api/v1/atoms", json={"values": {"name": "Admin's", "code": "ABC-002"}})
    assert admin.get("/internal/review-queue").json()[0]["can_review"] is True
    a = admin.post(f"/internal/change-requests/{r.json()['id']}/approve", json={})
    assert a.status_code == 200, a.text


def test_update_reject_and_stale_requests(admin, editor, reviewer, make_atom):
    setup_fields(admin)
    atom = make_atom({"name": "Thing", "code": "ABC-001", "size": 5})
    first = editor.patch(f"/api/v1/atoms/{atom['id']}", json={"values": {"size": 6}}, headers={"If-Match": '"1"'})
    second = editor.patch(f"/api/v1/atoms/{atom['id']}", json={"values": {"size": 7}})
    assert first.status_code == second.status_code == 202
    assert reviewer.post(f"/internal/change-requests/{first.json()['id']}/approve", json={}).status_code == 200
    stale = reviewer.post(f"/internal/change-requests/{second.json()['id']}/approve", json={})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale"
    # the submitter updates the request, which moves it onto the current revision
    assert editor.patch(f"/api/v1/change-requests/{second.json()['id']}", json={"values": {"size": 8}}).status_code == 200
    assert reviewer.post(f"/internal/change-requests/{second.json()['id']}/approve", json={}).status_code == 200
    r = editor.get(f"/api/v1/atoms/{atom['id']}")
    assert r.json()["values"]["size"] == 8 and r.headers["ETag"] == '"3"'
    # editing from an old revision is refused up front
    old = editor.patch(f"/api/v1/atoms/{atom['id']}", json={"values": {"size": 9}, "base_revision": 1})
    assert old.status_code == 409
    rej = editor.patch(f"/api/v1/atoms/{atom['id']}", json={"values": {"size": 10}}).json()
    assert reviewer.post(f"/internal/change-requests/{rej['id']}/reject", json={"comment": "no"}).json()["status"] == "rejected"
    revs = editor.get(f"/api/v1/atoms/{atom['id']}/revisions").json()
    assert [r["revision"] for r in revs] == [3, 2, 1] and revs[0]["values"]["size"] == 8


def test_deprecate_and_restore(admin, editor, reviewer, make_atom):
    setup_fields(admin)
    atom = make_atom({"name": "Old", "code": "OLD-001"})
    cr = editor.post(f"/api/v1/atoms/{atom['id']}/deprecate", json={}).json()
    reviewer.post(f"/internal/change-requests/{cr['id']}/approve", json={})
    assert editor.get("/api/v1/atoms").json()["total"] == 0
    assert editor.get("/api/v1/atoms?status=deprecated").json()["total"] == 1
    assert editor.patch(f"/api/v1/atoms/{atom['id']}", json={"values": {"size": 2}}).status_code == 409
    cr = editor.post(f"/api/v1/atoms/{atom['id']}/restore", json={}).json()
    reviewer.post(f"/internal/change-requests/{cr['id']}/approve", json={})
    assert editor.get("/api/v1/atoms").json()["total"] == 1


def test_withdraw(admin, editor, reviewer):
    setup_fields(admin)
    cr = editor.post("/api/v1/atoms", json={"values": {"name": "X", "code": "ABC-001"}}).json()
    assert editor.post(f"/api/v1/change-requests/{cr['id']}/withdraw").json()["status"] == "withdrawn"
    assert reviewer.post(f"/internal/change-requests/{cr['id']}/approve", json={}).status_code == 409


def test_search_and_filters(admin, editor, make_atom):
    setup_fields(admin)
    make_atom({"name": "First name", "code": "PER-001", "size": 100, "since": "2024-01-01", "tags": ["pii"]})
    make_atom({"name": "Last name", "code": "PER-002", "size": 100, "since": "2025-06-01", "tags": ["pii"]})
    make_atom({"name": "Annual income", "code": "FIN-001", "size": 12, "since": "2025-09-01", "tags": ["fin", "pii"]})

    def names(params):
        r = editor.get("/api/v1/atoms", params=params)
        assert r.status_code == 200, r.text
        return sorted(a["values"]["name"] for a in r.json()["items"])

    assert names({"q": "nam"}) == ["First name", "Last name"]
    assert names({"q": "personal"}) == ["Annual income", "First name", "Last name"]  # option labels are searchable
    assert names({"filter[tags]": "fin"}) == ["Annual income"]
    assert names({"filter[size][lt]": "50"}) == ["Annual income"]
    assert names({"filter[since][gte]": "2025-01-01", "filter[size]": "100"}) == ["Last name"]
    assert names({"filter[code][in]": "PER-001,FIN-001"}) == ["Annual income", "First name"]
    assert names({"filter[code][contains]": "per"}) == ["First name", "Last name"]
    r = editor.get("/api/v1/atoms", params={"sort": "-since", "page_size": 2})
    body = r.json()
    assert body["total"] == 3 and [a["values"]["code"] for a in body["items"]] == ["FIN-001", "PER-002"]
    assert editor.get("/api/v1/atoms", params={"filter[nope]": "1"}).status_code == 400
    assert editor.get("/api/v1/atoms", params={"filter[size][gte]": "big"}).status_code == 400


def test_time_dependent_fields_are_calculated_when_read(admin, editor, make_atom, db):
    from app.models import Atom

    setup_fields(admin)
    atom = make_atom({"name": "T", "code": "TIM-001", "since": "2020-01-01"})
    stored = db.get(Atom, atom["id"])
    stored.values = dict(stored.values, age=1)  # pretend the stored copy is out of date
    db.commit()
    fresh = editor.get(f"/api/v1/atoms/{atom['id']}").json()
    assert fresh["values"]["age"] > 1000
    assert admin.post("/internal/jobs/refresh-time-dependent").json()["updated_atoms"] == 1


def test_preview_validates_and_calculates_without_saving(admin, editor):
    setup_fields(admin)
    ok = editor.post("/api/v1/atoms/preview", json={"values": {"name": "P", "code": "ABC-001", "since": "2026-01-01"}}).json()
    assert ok["valid"] and ok["values"]["age"] >= 0
    bad = editor.post("/api/v1/atoms/preview", json={"values": {"name": "P", "code": "nope"}}).json()
    assert not bad["valid"] and bad["errors"][0]["field"] == "code"
    assert editor.get("/api/v1/atoms").json()["total"] == 0
