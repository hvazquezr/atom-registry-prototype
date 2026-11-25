def test_requests_need_a_token(client):
    assert client.get("/api/v1/atoms").status_code == 401
    r = client.get("/api/v1/atoms", headers={"Authorization": "Bearer atr_nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"


def test_wrong_password(client):
    r = client.post("/internal/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_scopes_follow_roles(editor, reviewer):
    assert editor.post("/internal/fields", json={"key": "x", "label": "X", "type": "text"}).status_code == 403
    assert editor.get("/internal/review-queue").status_code == 403
    assert reviewer.get("/internal/review-queue").status_code == 200


def test_api_tokens_are_limited_to_role_scopes_and_revocable(client, editor):
    r = editor.post("/internal/tokens", json={"name": "too much", "scopes": ["schema:admin"]})
    assert r.status_code == 403
    r = editor.post("/internal/tokens", json={"name": "read only", "scopes": ["atoms:read"]})
    assert r.status_code == 201
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/atoms", headers=h).status_code == 200
    assert client.post("/api/v1/atoms", headers=h, json={"values": {"name": "x"}}).status_code == 403
    assert editor.delete(f"/internal/tokens/{r.json()['id']}").status_code == 204
    assert client.get("/api/v1/atoms", headers=h).status_code == 401


def test_logout_revokes_session(editor):
    assert editor.post("/internal/auth/logout").status_code == 204
    assert editor.get("/internal/auth/me").status_code == 401


def test_admin_creates_service_account_with_token(client, admin):
    r = admin.post("/internal/users", json={"username": "forms-service", "roles": ["editor"], "is_service_account": True})
    assert r.status_code == 201
    t = admin.post("/internal/tokens", json={"name": "forms", "user_id": r.json()["id"]})
    assert t.status_code == 201 and set(t.json()["scopes"]) == {"atoms:read", "atoms:write"}
