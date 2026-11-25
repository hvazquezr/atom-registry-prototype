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
- **Forms.** Atoms grouped into ordered sections. Editors change a form's draft freely; publishing a
  version is a change request. Relations shape the form: *Depends on* pulls in the atoms an atom needs,
  *Includes* groups atoms under their parent, and *Required if* hides an atom until another answer
  meets the link's condition (e.g. `value == true`). Published versions are frozen, pinning each atom's
  revision.
- **Calculated atoms.** An atom with a **Formula** gets its answer on a form from other atoms' answers.
  Refer to other atoms with `@` and their name in lowercase with underscores, e.g. `@weight / (@height * @height)`;
  typing `@` in the formula box suggests atoms. When the atom is approved, each `@name` becomes a
  *Calculated from* link (kept in step with the formula, never edited by hand), so renaming an input
  atom later doesn't break the formula. On a form the atom is read-only and fills in once every input is
  answered; publishing is blocked until every input atom is on the form, and loops are refused.
- **Records.** Saved answers to a published form. Forms are the record types, so there's nothing new
  to define: pick a form on the *Records* page, click *New record*, fill it in and save. Each save is
  checked like the form preview (types, rules, *Required if*, calculated answers worked out on save).
  A bad answer blocks the save; missing required answers don't, and the record shows as *incomplete*
  until they're filled. Records save directly without review, every save is kept in the record's
  history, and records can be archived and restored. A record stays on the form version it was saved
  with until someone moves it to the latest one, which checks it again.
- **Reviews.** Every create, edit, relation change or deprecation of an atom, and every publish or
  deprecation of a form, is a change request. Anyone with the reviewer role can approve it, except the
  person who submitted it. Admins can approve their own requests too.
- **History.** Every approved change writes a full revision (values and relations).

## Run it

With Docker:

```bash
docker compose up --build
```

Then open http://localhost:5173 and sign in as `admin`, `editor`, `reviewer` or `records` (password `changeme`,
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
| `form`, `form_section`, `form_item` | A form's draft: sections, the atoms in them, and per-form label, help text and "always required" |
| `form_version` | A published form, fully resolved: items, input rules, conditions, pinned atom revisions and values |
| `record`, `record_revision` | Saved answers to a form version (JSONB, GIN-indexed), what's still missing, status; a snapshot after every save |
| `app_setting` | Admin settings, such as which atom fields decide a form input's type and rules |
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

**Forms** are checked every time the draft is saved, and the checks are shown in the builder:

| Check | Effect |
| --- | --- |
| An atom *Depends on* an atom that isn't on the form | Blocks publishing (one click adds it) |
| An atom is *Required if* an atom that isn't on the form | Blocks publishing (one click adds it) |
| A deprecated atom, an empty form, a conditional atom also marked "always required", or atoms that are only *Required if* each other (never shown) | Blocks publishing |
| An atom *Includes* an atom that isn't on the form, a trigger placed after the atom it controls, an empty section | Warning |

Each atom becomes an input whose type and rules come from the atom's own values. By default the
`data_type` field picks the type (`string`→text, `integer`, `decimal`→number, `date`, `boolean`→yes/no)
and `max_length` limits text. Schema admins change this mapping under *Forms → How atoms become
questions*. A *Required if* link carries an optional CEL `condition` over `value`, the other atom's
answer; without one it means "when the other atom is answered". When that holds, the atom is shown and
required; otherwise it's hidden and its answer is ignored.

When an atom changes after a form is published, the published version keeps the old revision and the
form page offers to publish a new version with the update.

## Auth

Send `Authorization: Bearer <token>` on every request. The web app gets a session token from
`POST /internal/auth/login`. Scripts and other systems use API tokens, created on the API tokens page
or with `POST /internal/tokens`. A token can have fewer scopes than its user, never more.

| Role | Scopes |
| --- | --- |
| viewer | `atoms:read`, `forms:read`, `records:read` |
| atom_editor (Atom editor) | `atoms:read`, `atoms:write` (submit change requests), `forms:read`, `forms:write` (build forms, submit them) |
| reviewer | `atoms:read`, `atoms:approve` (atoms and forms), `forms:read` |
| record_manager (Record manager) | `atoms:read`, `forms:read`, `records:read`, `records:write` (create, edit, archive and restore records) |
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

# forms: the resolved form, ready to render (a version number, latest, or draft)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/forms/person_details/versions/latest

# check answers: returns visible/required questions, cleaned answers and errors (nothing is stored)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  localhost:8000/api/v1/forms/person_details/validate -d '{
    "answers": {"first_name": "Ada", "last_name": "Lovelace", "has_middle_name": true}
  }'
# → {"valid": false, "errors": [{"field": "middle_name", "code": "required", ...}], ...}

# build and publish a form (publishing opens a change request for a reviewer)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  localhost:8000/api/v1/forms -d '{"key": "signup", "name": "Signup",
    "sections": [{"title": "You", "items": [{"atom_id": "<uuid>", "required": true}]}]}'
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  localhost:8000/api/v1/forms/signup/publish -d '{"notes": "First version"}'
```

```bash
# records: each form's records live under /api/v1/data/<form key>.
# A new record is saved against the form's latest version; missing required answers are allowed.
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  localhost:8000/api/v1/data/person_details -d '{"answers": {"first_name": "Grace"}}'
# → 201 {"id": "...", "form_version": 1, "complete": false, "missing": ["last_name"], ...}

# search a form's records by text and answers
curl -H "Authorization: Bearer $TOKEN" \
  "localhost:8000/api/v1/data/person_details?q=hopper&filter[annual_income][gte]=50000&complete=true"

# change some answers (null clears one), or move to the latest form version with "form_version": "latest"
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "If-Match: 1" -H "Content-Type: application/json" \
  localhost:8000/api/v1/data/person_details/<uuid> -d '{"answers": {"last_name": "Hopper"}}'

# history; archive and restore
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/data/person_details/<uuid>/revisions
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  localhost:8000/api/v1/data/person_details/<uuid>/archive -d '{}'

# the same routes also exist without the form, for searching across forms: /api/v1/records?q=...
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

- Login through the larger system's identity provider.
- A scheduler for the daily refresh; use cron or the endpoint.
- Approval for schema changes (admins publish directly).
