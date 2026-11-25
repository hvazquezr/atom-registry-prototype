# Atom Registry (prototype)

A registry of **atoms**: data elements that can be grouped into forms. Admins define which fields an
atom has (the *meta atom*), people manage atoms through a reviewed workflow, and other systems use a
token-protected API to search, read, create and update them.

- **Meta atom.** Fields of type text, number, date, list or options (single or multiple choice).
  Each can be mandatory or optional, with range, length and regex rules. Calculated fields are single
  [CEL](https://cel.dev) expressions over other fields, with `today()` and `now()`. Field edits go to a
  draft and become live when the schema is published as a new version.
- **Relations.** Typed links between atoms: *Depends on*, *Includes* and *Required if* out of the box,
  each with a note. Admins can add types with their own direction, cardinality and loop rules.
- **Reviews.** Every create, edit, relation change or deprecation is a change request. Anyone with
  the reviewer role can approve it, except the person who submitted it.
- **History.** Every approved change writes a full revision (values and relations).

## Run it

With Docker:

```bash
docker compose up --build
```

Then open http://localhost:5173 and sign in as `admin`, `editor` or `reviewer` (password `changeme`,
set with `ATOM_SEED_PASSWORD`). Demo fields and atoms are loaded unless `SEED_DEMO=` is empty. The API
docs are at http://localhost:8000/docs.

Without Docker (Python 3.12+, Node 22+, Postgres 16):

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # point ATOM_DATABASE_URL at your database
alembic upgrade head
python -m app.cli seed --demo
uvicorn app.main:app --reload     # http://localhost:8000

cd ../frontend
npm install && npm run dev        # http://localhost:5173
```

Tests use a separate database (`atoms_test` by default; override with `ATOM_DATABASE_URL`):

```bash
cd backend && pytest
```

## How it fits together

```
React app ──/internal──┐
                       ├── FastAPI ── services ── Postgres
Other systems ─/api/v1─┘
```

| Table | Holds |
| --- | --- |
| `field_definition` | Draft field definitions: type, rules (`config` JSONB), expression, dependencies |
| `schema_version` | Published snapshots of the fields; atoms record the version they were validated against |
| `relation_type` | Link types: labels both ways, direction, cardinality, loops allowed, note required |
| `atom` | Current values (JSONB, GIN-indexed), status, revision, full-text search vector |
| `atom_relation` | Edges: type, source, target, note, attributes |
| `change_request` | Proposed values and relation changes, base revision, review outcome |
| `atom_revision` | Snapshot of values and relations after each approved change |
| `app_user`, `api_token` | Users with roles; hashed tokens with scopes |

**Validation** runs on submission and again on approval: unknown or read-only keys, required, type,
ranges and lengths, regex (RE2, so patterns can't hang the server), options, then calculated fields,
then rules on the calculated results. All problems come back together in one 422 response.

**Calculated fields** are parsed when saved. The server rejects unknown fields or functions,
self-references and loops between calculated fields, and dry-runs the expression with sample values
to catch type errors. Expressions have no I/O, and their length and size are capped. Extra functions:
`today()`, `now()`, `date("2026-01-31")`, `days_between(a, b)`. Fields that use `today()` or `now()`,
directly or through another field, are recalculated whenever an atom is read. To keep their stored
copy searchable, run `python -m app.cli refresh-time-dependent` daily (cron) or call
`POST /internal/jobs/refresh-time-dependent`. Publishing a changed expression recalculates existing
atoms in the background.

**Relations** are checked on submission and on approval: no self-links or duplicates, cardinality,
and no loops for types that refuse them. `GET /api/v1/atoms/{id}/graph?depth=3` walks the links with
a recursive query.

## Auth

Send `Authorization: Bearer <token>` on every request. The web app gets a session token from
`POST /internal/auth/login`. Scripts and other systems use API tokens, created on the API tokens page
or with `POST /internal/tokens`. A token can have fewer scopes than its user, never more.

| Role | Scopes |
| --- | --- |
| viewer | `atoms:read` |
| editor | `atoms:read`, `atoms:write` (submit change requests) |
| reviewer | `atoms:read`, `atoms:approve` |
| admin | all, including `schema:admin` and `users:admin` |

An external identity provider (OIDC/JWT) can be added later in `app/auth.py` without changing routes.

## API examples

```bash
TOKEN=atr_...
# search: text plus field filters
curl -H "Authorization: Bearer $TOKEN" \
  "localhost:8000/api/v1/atoms?q=name&filter[tags]=pii&filter[max_length][gte]=50&sort=-updated_at"

# propose a new atom with a relation (returns 202 and the change request)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  localhost:8000/api/v1/atoms -d '{
    "values": {"name": "Email", "code": "CON-001", "data_type": "string"},
    "relations": [{"type": "dependency", "target_atom_id": "<uuid>", "note": "Validated against it"}]
  }'

# propose an edit (only the fields that change; null clears one)
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "If-Match: 3" -H "Content-Type: application/json" \
  localhost:8000/api/v1/atoms/<uuid> -d '{"values": {"max_length": 320}}'
```

Filter operators: `eq` (default), `ne`, `in` (comma-separated), `gt`, `gte`, `lt`, `lte`,
`contains`, `exists`. The full reference is at `/docs`.

## Settings

All settings are environment variables (see `backend/.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `ATOM_DATABASE_URL` | local `atoms` database | SQLAlchemy URL |
| `ATOM_TIMEZONE` | `UTC` | Time zone for `today()` |
| `ATOM_ALLOW_SELF_APPROVAL` | `false` | Let reviewers approve their own requests |
| `ATOM_SESSION_TOKEN_HOURS` | `12` | Lifetime of web sessions |
| `ATOM_MAX_GRAPH_DEPTH` | `5` | Deepest graph traversal allowed |
| `ATOM_CORS_ORIGINS` | `http://localhost:5173` | Browser origins allowed to call the API |

## Not in this prototype

- Forms that group atoms (the tables above leave room for `form` and `form_atom`).
- Login through the larger system's identity provider.
- A scheduler for the daily refresh; use cron or the endpoint.
- Approval for schema changes (admins publish directly).
