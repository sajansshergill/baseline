"""Partitions, indexes, and the snapshot_current view.

Revision ID: 0003
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PARTITIONED = ["activity", "relationship", "activity_cpm"]
PARTITIONS = 4


def upgrade() -> None:
    for table in PARTITIONED:
        for i in range(PARTITIONS):
            op.execute(
                f"CREATE TABLE {table}_p{i} PARTITION OF {table} "
                f"FOR VALUES WITH (MODULUS {PARTITIONS}, REMAINDER {i});"
            )

    op.execute(
        """
        -- The diff, health, and feature queries all filter by project and data date.
        CREATE INDEX ix_snapshot_project_date
            ON snapshot (tenant_id, project_id, data_date, ingested_at DESC);

        -- Following one activity across many snapshots.
        CREATE INDEX ix_activity_history ON activity (tenant_id, activity_id);

        -- Walking logic backwards (successor -> predecessors).
        CREATE INDEX ix_relationship_successor
            ON relationship (tenant_id, snapshot_id, successor_id);

        CREATE INDEX ix_progress_activity
            ON progress_report (tenant_id, project_id, activity_id, report_date);

        CREATE INDEX ix_quarantine_upload ON quarantine_event (tenant_id, upload_id);

        -- "What does the schedule say for each data date, as we know it now?"
        -- If an update was re-issued, the most recently ingested version wins.
        -- security_invoker makes row-level security apply to whoever queries the view.
        CREATE VIEW snapshot_current WITH (security_invoker = true) AS
        SELECT DISTINCT ON (tenant_id, project_id, data_date) *
        FROM snapshot
        ORDER BY tenant_id, project_id, data_date, ingested_at DESC;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW snapshot_current;")
    op.execute(
        "DROP INDEX ix_snapshot_project_date, ix_activity_history, ix_relationship_successor, "
        "ix_progress_activity, ix_quarantine_upload;"
    )
    for table in PARTITIONED:
        for i in range(PARTITIONS):
            op.execute(f"DROP TABLE {table}_p{i};")