"""Core tables: tenant, project, upload, snapshot, quarantine_event.

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tenant (
            tenant_id   text PRIMARY KEY,
            name        text NOT NULL,
            region      text NOT NULL DEFAULT 'us-east-1',
            created_at  timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE project (
            tenant_id   text NOT NULL REFERENCES tenant,
            project_id  text NOT NULL,
            name        text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, project_id)
        );

        -- One row per distinct file a tenant sent us. The same bytes twice = one row.
        CREATE TABLE upload (
            upload_id       uuid PRIMARY KEY,
            tenant_id       text NOT NULL REFERENCES tenant,
            project_id      text,
            filename        text NOT NULL,
            source_format   text,
            content_sha256  char(64) NOT NULL,
            storage_key     text NOT NULL,
            status          text NOT NULL
                            CHECK (status IN ('received', 'loaded', 'quarantined')),
            warnings        jsonb NOT NULL DEFAULT '[]',
            received_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, content_sha256)
        );

        -- One schedule as of one data date. Never updated; a correction is a new row.
        CREATE TABLE snapshot (
            snapshot_id         uuid PRIMARY KEY,
            tenant_id           text NOT NULL,
            project_id          text NOT NULL,
            upload_id           uuid NOT NULL REFERENCES upload,
            data_date           date NOT NULL,          -- valid time
            ingested_at         timestamptz NOT NULL DEFAULT now(),  -- transaction time
            activity_count      integer NOT NULL,
            relationship_count  integer NOT NULL,
            FOREIGN KEY (tenant_id, project_id) REFERENCES project,
            UNIQUE (tenant_id, upload_id)
        );

        CREATE TABLE quarantine_event (
            event_id     uuid PRIMARY KEY,
            tenant_id    text NOT NULL REFERENCES tenant,
            upload_id    uuid NOT NULL REFERENCES upload ON DELETE CASCADE,
            reason_code  text NOT NULL,
            rule         text NOT NULL,
            detail       text NOT NULL,
            sample       jsonb NOT NULL DEFAULT '[]',
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE quarantine_event, snapshot, upload, project, tenant CASCADE;"
    )