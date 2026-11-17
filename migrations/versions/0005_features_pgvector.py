"""ML tables: activity_feature, slip_prediction, delay_note_embedding (pgvector).

Revision ID: 0005
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 256


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        f"""
        CREATE TABLE activity_feature (
            tenant_id                text NOT NULL,
            project_id               text NOT NULL,
            as_of_date               date NOT NULL,
            activity_id              text NOT NULL,
            total_float_days         double precision,
            float_change_4w          double precision,
            min_float_8w             double precision,
            percent_complete         double precision,
            progress_velocity_4w     double precision,
            remaining_duration_days  double precision,
            finish_slip_count        double precision,
            finish_slip_days         double precision,
            pred_count               double precision,
            late_pred_count          double precision,
            is_critical              double precision,
            weeks_tracked            double precision,
            has_delay_note           double precision,
            is_milestone             double precision,
            PRIMARY KEY (tenant_id, project_id, as_of_date, activity_id)
        );

        CREATE TABLE slip_prediction (
            tenant_id         text NOT NULL,
            project_id        text NOT NULL,
            as_of_date        date NOT NULL,
            activity_id       text NOT NULL,
            model_version     text NOT NULL,
            slip_probability  double precision NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, project_id, as_of_date, activity_id, model_version)
        );

        CREATE TABLE delay_note_embedding (
            tenant_id    text NOT NULL,
            snapshot_id  uuid NOT NULL REFERENCES snapshot ON DELETE CASCADE,
            activity_id  text NOT NULL,
            project_id   text NOT NULL,
            data_date    date NOT NULL,
            note         text NOT NULL,
            embedding    vector({EMBEDDING_DIM}) NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id, activity_id)
        );

        -- Approximate nearest-neighbour index for cosine similarity search.
        CREATE INDEX ix_delay_note_embedding
            ON delay_note_embedding USING hnsw (embedding vector_cosine_ops);
        """
    )
    # Same tenant isolation policy as 0004.
    policy = "tenant_id = current_setting('app.tenant_id', true)"
    for table in ("activity_feature", "slip_prediction", "delay_note_embedding"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({policy}) WITH CHECK ({policy});"
        )


def downgrade() -> None:
    op.execute("DROP TABLE delay_note_embedding, slip_prediction, activity_feature;")