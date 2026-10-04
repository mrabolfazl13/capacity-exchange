"""0001 core schema (CONTRACTS §5)

Revision ID: 0001
Revises:
Create Date: 2026-10-05

The table set is derived from the model metadata so schema and mappers cannot drift.
Everything Postgres-specific that SQLAlchemy cannot express — the native booking
enums, the `set_updated_at()` trigger, the generated `search_vector` — is applied
explicitly around it. No contrib extension is required anywhere: pgserver 16.2 ships
core only (see §5.5 for why booking safety uses advisory locks instead of btree_gist).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models import Base

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def _updated_at_tables() -> list[str]:
    return sorted(t.name for t in Base.metadata.tables.values() if "updated_at" in t.columns)

BOOKING_STATUS_VALUES = ("draft", "hold", "confirmed", "in_progress", "completed",
                         "cancelled", "expired", "disputed")
BOOKING_PAYMENT_VALUES = ("not_required", "unpaid", "paid", "refunded", "partially_refunded")


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(
        "CREATE TYPE bookings_status_enum AS ENUM ("
        + ", ".join(f"'{v}'" for v in BOOKING_STATUS_VALUES) + ")")
    op.execute(
        "CREATE TYPE bookings_payment_enum AS ENUM ("
        + ", ".join(f"'{v}'" for v in BOOKING_PAYMENT_VALUES) + ")")

    op.execute("""
        CREATE FUNCTION set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$;
    """)

    # create_all skips CREATE TYPE because both enums declare create_type=False.
    Base.metadata.create_all(bind)

    for table in _updated_at_tables():
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()")

    # Marketplace full-text search: core to_tsvector, no pg_trgm/unaccent dependency.
    op.execute("""
        ALTER TABLE offers ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(description, '')), 'B')
            ) STORED;
    """)
    op.execute("CREATE INDEX ix_offers_search_vector ON offers USING gin (search_vector)")

    # Read-only convenience view over the availability expansion (§5.5). The correctness
    # path stays in the capacity service; nothing depends on this view existing.
    op.execute("""
        CREATE VIEW capacity_availability AS
        SELECT ra.definition_id,
               ra.dow,
               ra.start_time,
               ra.end_time,
               ra.quantity,
               cd.max_quantity,
               cr.org_id,
               cr.status AS resource_status,
               cd.is_active AS definition_active
        FROM recurring_availabilities ra
        JOIN capacity_definitions cd ON cd.id = ra.definition_id
        JOIN capacity_resources cr ON cr.id = cd.resource_id
        WHERE ra.is_active AND cd.is_active AND cr.status = 'active';
    """)


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP VIEW IF EXISTS capacity_availability")
    for table in _updated_at_tables():
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    Base.metadata.drop_all(bind)
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    op.execute("DROP TYPE IF EXISTS bookings_payment_enum")
    op.execute("DROP TYPE IF EXISTS bookings_status_enum")
