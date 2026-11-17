"""Row-level security: every query only sees the current tenant's rows.

The app sets the tenant at the start of each transaction:

    SELECT set_config('app.tenant_id', 'demo_owner', true);

If it forgets, current_setting(..., true) returns NULL and the query sees nothing.
FORCE makes the policy apply to the table owner too (our app role owns the tables).

Revision ID: 0004
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "tenant",
    "project",
    "upload",
    "snapshot",
    "quarantine_event",
    "activity",
    "relationship",
    "calendar",
    "activity_cpm",
    "progress_report",
]

POLICY = "tenant_id = current_setting('app.tenant_id', true)"


def enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY}) WITH CHECK ({POLICY});"
    )


def disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY tenant_isolation ON {table};")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")


def upgrade() -> None:
    for table in TENANT_TABLES:
        enable_rls(table)


def downgrade() -> None:
    for table in TENANT_TABLES:
        disable_rls(table)