"""One Dagster partition per snapshot: tenant × project × data date.

Partition keys look like  demo_owner|HYPERSCALE_CAMPUS|2026-03-02
That lets you re-run (backfill) any single update, or any range of them, from the UI.
"""

from datetime import date

from dagster import DynamicPartitionsDefinition

snapshot_partitions = DynamicPartitionsDefinition(name="snapshots")


def partition_key(tenant_id: str, project_id: str, data_date: date) -> str:
    return f"{tenant_id}|{project_id}|{data_date.isoformat()}"


def parse_partition_key(key: str) -> tuple[str, str, date]:
    tenant_id, project_id, day = key.split("|")
    return tenant_id, project_id, date.fromisoformat(day)