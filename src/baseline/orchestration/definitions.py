"""Dagster entrypoint: `dagster dev -m baseline.orchestration.definitions`."""

from dagster import Definitions, load_assets_from_modules

from baseline.orchestration import assets as assets_module
from baseline.orchestration.checks import ALL_CHECKS
from baseline.orchestration.resources import default_resource
from baseline.orchestration.schedules import ALL_SCHEDULES, nightly_models_job, nightly_quality_job
from baseline.orchestration.sensors import (
    inbox_sensor,
    ingest_file_job,
    snapshot_analytics_job,
    snapshot_partition_sensor,
)

defs = Definitions(
    assets=load_assets_from_modules([assets_module]),
    asset_checks=ALL_CHECKS,
    jobs=[ingest_file_job, snapshot_analytics_job, nightly_models_job, nightly_quality_job],
    sensors=[inbox_sensor, snapshot_partition_sensor],
    schedules=ALL_SCHEDULES,
    resources={"baseline": default_resource()},
)
