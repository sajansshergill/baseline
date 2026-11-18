"""Time-based Dagster schedules for model refresh and quality monitors."""

from dagster import DefaultScheduleStatus, ScheduleDefinition, define_asset_job

from baseline.orchestration.assets import quality_report, slip_models, slip_predictions

nightly_models_job = define_asset_job(
    "nightly_models_job",
    selection=[slip_models, slip_predictions],
)

nightly_quality_job = define_asset_job(
    "nightly_quality_job",
    selection=[quality_report],
)

nightly_models_schedule = ScheduleDefinition(
    name="nightly_models",
    job=nightly_models_job,
    cron_schedule="0 2 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
    description="Retrain slip models and refresh predictions every night at 02:00",
)

nightly_quality_schedule = ScheduleDefinition(
    name="nightly_quality",
    job=nightly_quality_job,
    cron_schedule="30 2 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
    description="Run data-quality monitors every night at 02:30",
)

ALL_SCHEDULES = [nightly_models_schedule, nightly_quality_schedule]
