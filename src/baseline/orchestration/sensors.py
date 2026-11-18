"""Sensors: react to new files and new snapshots.

inbox_sensor
    Watches  inbox/<tenant>/  in storage (local folder or S3). Every new file starts one
    ingest_file_job run. The run_key is the file's path, so Dagster never ingests the same
    inbox file twice. (Even if it did, the pipeline itself dedupes by content hash.)

snapshot_partition_sensor
    Looks for loaded snapshots that don't have a Dagster partition yet (from the inbox, or
    loaded with the CLI), adds the partitions, and runs the per-snapshot analytics.
"""

from dagster import (
    OpExecutionContext,
    RunRequest,
    SensorEvaluationContext,
    SensorResult,
    define_asset_job,
    job,
    op,
    sensor,
)

from baseline.db.reader import list_projects
from baseline.db.session import tenant_connection
from baseline.db.writer import current_snapshots
from baseline.ingest.landing import get_store
from baseline.orchestration.assets import (
    activity_features,
    snapshot_cpm,
    snapshot_diff,
    snapshot_health,
)
from baseline.orchestration.partitions import partition_key, snapshot_partitions
from baseline.orchestration.resources import BaselineResource
from baseline.pipeline import ingest_bytes

INBOX = "inbox"


@op(config_schema={"tenant_id": str, "key": str})
def ingest_file(context: OpExecutionContext) -> None:
    tenant_id, key = context.op_config["tenant_id"], context.op_config["key"]
    store = get_store()
    result = ingest_bytes(tenant_id, key.rsplit("/", 1)[-1], store.get(key), store=store)
    context.log.info(f"{result.outcome}: {result.filename} {result.reason_code or ''}")


@job(description="Ingest one file dropped into the inbox")
def ingest_file_job():
    ingest_file()


snapshot_analytics_job = define_asset_job(
    "snapshot_analytics_job",
    selection=[snapshot_cpm, snapshot_health, snapshot_diff, activity_features],
    partitions_def=snapshot_partitions,
)


@sensor(job=ingest_file_job, minimum_interval_seconds=30)
def inbox_sensor(context: SensorEvaluationContext, baseline: BaselineResource):
    store = get_store()
    for tenant in baseline.tenant_list:
        for key in store.list(f"{INBOX}/{tenant}/"):
            yield RunRequest(
                run_key=key,
                run_config={"ops": {"ingest_file": {"config": {"tenant_id": tenant, "key": key}}}},
            )


@sensor(job=snapshot_analytics_job, minimum_interval_seconds=60)
def snapshot_partition_sensor(context: SensorEvaluationContext, baseline: BaselineResource):
    existing = set(context.instance.get_dynamic_partitions(snapshot_partitions.name))
    new_keys = []
    for tenant in baseline.tenant_list:
        with tenant_connection(tenant) as conn:
            for project in list_projects(conn):
                for snap in current_snapshots(conn, project):
                    key = partition_key(tenant, project, snap["data_date"])
                    if key not in existing:
                        new_keys.append(key)
    return SensorResult(
        run_requests=[RunRequest(partition_key=k, run_key=k) for k in new_keys],
        dynamic_partitions_requests=[snapshot_partitions.build_add_request(new_keys)]
        if new_keys
        else [],
    )