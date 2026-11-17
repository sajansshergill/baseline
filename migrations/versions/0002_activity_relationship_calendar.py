"""Schedule content: activity, relationship, calendar, activity_cpm, progress_report.

The big tables (activity, relationship, activity_cpm) are hash-partitioned by tenant_id.
The partitions themselves are created in 0003.

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE activity (
            tenant_id                text NOT NULL,
            snapshot_id              uuid NOT NULL REFERENCES snapshot ON DELETE CASCADE,
            activity_id              text NOT NULL,
            name                     text NOT NULL,
            wbs                      text,
            activity_type            text NOT NULL,
            status                   text NOT NULL,
            percent_complete         numeric(6, 2) NOT NULL,
            original_duration_days   numeric(10, 4) NOT NULL,
            remaining_duration_days  numeric(10, 4) NOT NULL,
            planned_start            date,
            planned_finish           date,
            actual_start             date,
            actual_finish            date,
            early_start              date,
            early_finish             date,
            late_start               date,
            late_finish              date,
            total_float_days         numeric(10, 4),
            constraint_type          text,
            constraint_date          date,
            calendar_id              text,
            delay_note               text,
            PRIMARY KEY (tenant_id, snapshot_id, activity_id)
        ) PARTITION BY HASH (tenant_id);

        CREATE TABLE relationship (
            tenant_id       text NOT NULL,
            snapshot_id     uuid NOT NULL REFERENCES snapshot ON DELETE CASCADE,
            predecessor_id  text NOT NULL,
            successor_id    text NOT NULL,
            rel_type        text NOT NULL CHECK (rel_type IN ('FS', 'SS', 'FF', 'SF')),
            lag_days        numeric(10, 4) NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, snapshot_id, predecessor_id, successor_id, rel_type)
        ) PARTITION BY HASH (tenant_id);

        CREATE TABLE calendar (
            tenant_id      text NOT NULL,
            snapshot_id    uuid NOT NULL REFERENCES snapshot ON DELETE CASCADE,
            calendar_id    text NOT NULL,
            name           text NOT NULL,
            hours_per_day  numeric(5, 2) NOT NULL,
            work_days      smallint[] NOT NULL,
            holidays       date[] NOT NULL DEFAULT '{}',
            PRIMARY KEY (tenant_id, snapshot_id, calendar_id)
        );

        -- Baseline's own critical-path results, so every source format is scored the same way.
        CREATE TABLE activity_cpm (
            tenant_id         text NOT NULL,
            snapshot_id       uuid NOT NULL REFERENCES snapshot ON DELETE CASCADE,
            activity_id       text NOT NULL,
            early_start       date,
            early_finish      date,
            late_start        date,
            late_finish       date,
            total_float_days  numeric(10, 4),
            is_critical       boolean NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id, activity_id)
        ) PARTITION BY HASH (tenant_id);

        CREATE TABLE progress_report (
            tenant_id     text NOT NULL,
            upload_id     uuid NOT NULL REFERENCES upload ON DELETE CASCADE,
            source_row    integer NOT NULL,
            project_id    text NOT NULL,
            trade         text NOT NULL,
            activity_id   text NOT NULL,
            description   text,
            reported_pct  numeric(6, 2) NOT NULL,
            report_date   date NOT NULL,
            remark        text,
            PRIMARY KEY (tenant_id, upload_id, source_row)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE progress_report, activity_cpm, calendar, relationship, activity;")