import os

os.environ.setdefault("ATOM_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/atoms_test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401
from app.cli import seed  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "test-password"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean():
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    with SessionLocal() as db:
        seed(db, demo=False, password=PASSWORD)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


class Api:
    """TestClient wrapper that logs in as a seeded user."""

    def __init__(self, client: TestClient, username: str):
        self.client = client
        r = client.post("/internal/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.token = r.json()["token"]
        self.user = r.json()["user"]

    def _h(self, kw):
        kw.setdefault("headers", {})["Authorization"] = f"Bearer {self.token}"
        return kw

    def get(self, url, **kw):
        return self.client.get(url, **self._h(kw))

    def post(self, url, **kw):
        return self.client.post(url, **self._h(kw))

    def patch(self, url, **kw):
        return self.client.patch(url, **self._h(kw))

    def delete(self, url, **kw):
        return self.client.delete(url, **self._h(kw))


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client):
    return Api(client, "admin")


@pytest.fixture
def editor(client):
    return Api(client, "editor")


@pytest.fixture
def reviewer(client):
    return Api(client, "reviewer")


@pytest.fixture
def make_atom(editor, reviewer):
    """Create and approve an atom; returns its JSON."""

    def _make(values, relations=None):
        r = editor.post("/api/v1/atoms", json={"values": values, "relations": relations or []})
        assert r.status_code == 202, r.text
        a = reviewer.post(f"/internal/change-requests/{r.json()['id']}/approve", json={})
        assert a.status_code == 200, a.text
        return a.json()["atom"]

    return _make


def add_field(admin, **body):
    r = admin.post("/internal/fields", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def publish(admin):
    r = admin.post("/internal/schema/publish", json={"notes": "test"})
    assert r.status_code == 201, r.text
    return r.json()
