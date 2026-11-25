import pytest


@pytest.fixture
def atoms(make_atom):
    return {n: make_atom({"name": n}) for n in ("A", "B", "C", "D")}


def link(editor, reviewer, src, rtype, tgt, note="because", expect=200):
    cr = editor.patch(f"/api/v1/atoms/{src['id']}",
                      json={"relations": {"add": [{"type": rtype, "target_atom_id": tgt["id"], "note": note}]}})
    if cr.status_code != 202:
        return cr
    r = reviewer.post(f"/internal/change-requests/{cr.json()['id']}/approve", json={})
    assert r.status_code == expect, r.text
    return r


def test_relations_need_a_note(editor, atoms):
    r = editor.patch(f"/api/v1/atoms/{atoms['A']['id']}",
                     json={"relations": {"add": [{"type": "dependency", "target_atom_id": atoms["B"]["id"]}]}})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "note_required"


def test_add_list_and_remove_relations(editor, reviewer, atoms):
    link(editor, reviewer, atoms["A"], "dependency", atoms["B"], note="A needs B")
    link(editor, reviewer, atoms["A"], "includes", atoms["B"], note="and contains it")
    rels = editor.get(f"/api/v1/atoms/{atoms['A']['id']}/relations").json()
    assert {(r["type"], r["direction"], r["other_atom_name"], r["note"]) for r in rels} == {
        ("dependency", "out", "B", "A needs B"), ("includes", "out", "B", "and contains it")}
    incoming = editor.get(f"/api/v1/atoms/{atoms['B']['id']}/relations").json()
    assert {r["label"] for r in incoming} == {"Dependency of", "Included in"}
    dup = editor.patch(f"/api/v1/atoms/{atoms['A']['id']}",
                       json={"relations": {"add": [{"type": "dependency", "target_atom_id": atoms["B"]["id"], "note": "x"}]}})
    assert dup.status_code == 422 and dup.json()["error"]["details"][0]["code"] == "duplicate_relation"
    rid = next(r["id"] for r in rels if r["type"] == "dependency")
    cr = editor.patch(f"/api/v1/atoms/{atoms['A']['id']}", json={"relations": {"remove": [rid]}}).json()
    reviewer.post(f"/internal/change-requests/{cr['id']}/approve", json={})
    assert len(editor.get(f"/api/v1/atoms/{atoms['A']['id']}/relations").json()) == 1
    revs = editor.get(f"/api/v1/atoms/{atoms['A']['id']}/revisions").json()
    assert revs[0]["summary"] == "Updated relations" and len(revs[0]["relations"]) == 1


def test_dependency_loops_are_refused(editor, reviewer, atoms):
    link(editor, reviewer, atoms["A"], "dependency", atoms["B"])
    link(editor, reviewer, atoms["B"], "dependency", atoms["C"])
    r = link(editor, reviewer, atoms["C"], "dependency", atoms["A"])
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "cycle"
    # required_if allows loops
    link(editor, reviewer, atoms["A"], "required_if", atoms["B"])
    link(editor, reviewer, atoms["B"], "required_if", atoms["A"])


def test_cardinality(admin, editor, reviewer, atoms):
    r = admin.post("/internal/relation-types", json={"key": "replaces", "label": "Replaces", "inverse_label": "Replaced by",
                                                     "cardinality": "one_to_one", "require_note": False})
    assert r.status_code == 201
    link(editor, reviewer, atoms["A"], "replaces", atoms["B"], note="")
    r = link(editor, reviewer, atoms["A"], "replaces", atoms["C"], note="")
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "cardinality"
    r = link(editor, reviewer, atoms["D"], "replaces", atoms["B"], note="")
    assert r.status_code == 422


def test_self_links_and_unknown_targets(editor, atoms):
    r = editor.patch(f"/api/v1/atoms/{atoms['A']['id']}",
                     json={"relations": {"add": [{"type": "includes", "target_atom_id": atoms["A"]["id"], "note": "x"}]}})
    assert r.status_code == 422
    r = editor.patch(f"/api/v1/atoms/{atoms['A']['id']}", json={"relations": {"add": [
        {"type": "includes", "target_atom_id": "00000000-0000-0000-0000-000000000000", "note": "x"}]}})
    assert r.json()["error"]["details"][0]["code"] == "unknown_atom"


def test_create_with_relations(editor, reviewer, atoms):
    cr = editor.post("/api/v1/atoms", json={"values": {"name": "E"}, "relations": [
        {"type": "includes", "target_atom_id": atoms["A"]["id"], "note": "E has A"},
        {"type": "dependency", "target_atom_id": atoms["B"]["id"], "direction": "in", "note": "B needs E"}]}).json()
    atom = reviewer.post(f"/internal/change-requests/{cr['id']}/approve", json={}).json()["atom"]
    rels = editor.get(f"/api/v1/atoms/{atom['id']}/relations").json()
    assert {(r["label"], r["other_atom_name"]) for r in rels} == {("Includes", "A"), ("Dependency of", "B")}


def test_graph_traversal(editor, reviewer, atoms):
    link(editor, reviewer, atoms["A"], "dependency", atoms["B"])
    link(editor, reviewer, atoms["B"], "includes", atoms["C"])
    link(editor, reviewer, atoms["C"], "dependency", atoms["D"])
    g = editor.get(f"/api/v1/atoms/{atoms['A']['id']}/graph", params={"depth": 2}).json()
    assert {n["name"]: n["depth"] for n in g["nodes"]} == {"A": 0, "B": 1, "C": 2}
    assert len(g["edges"]) == 2
    g = editor.get(f"/api/v1/atoms/{atoms['A']['id']}/graph", params={"depth": 5, "types": "dependency"}).json()
    assert {n["name"] for n in g["nodes"]} == {"A", "B"}
    g = editor.get(f"/api/v1/atoms/{atoms['D']['id']}/graph", params={"depth": 5, "direction": "in"}).json()
    assert {n["name"] for n in g["nodes"]} == {"A", "B", "C", "D"}
