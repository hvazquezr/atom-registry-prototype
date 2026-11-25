"""at references in field expressions

Rewrites calculated field expressions in the draft to refer to other fields as @key, like atom
formulas. Bare names keep working, so this only changes how expressions read.

Revision ID: 5b1e2c7d9a40
Revises: 1378cd361682
Create Date: 2026-11-25 19:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '5b1e2c7d9a40'
down_revision: Union[str, Sequence[str], None] = '1378cd361682'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.services.expressions import with_at_refs

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, key, expression FROM field_definition")).all()
    keys = {r.key for r in rows}
    for r in rows:
        if not r.expression:
            continue
        new = with_at_refs(r.expression, keys - {r.key})
        if new != r.expression:
            conn.execute(sa.text("UPDATE field_definition SET expression = :e WHERE id = :id"), {"e": new, "id": r.id})


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, expression FROM field_definition WHERE expression LIKE '%@%'")).all()
    from app.services.expressions import translate_refs

    for r in rows:
        cel, _ = translate_refs(r.expression)
        conn.execute(sa.text("UPDATE field_definition SET expression = :e WHERE id = :id"), {"e": cel, "id": r.id})
