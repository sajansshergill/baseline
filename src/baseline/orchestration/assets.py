"""Each pipeline step as a Dagster asset.

Per snapshot (partitioned):
    snapshot_cpm  ->  snapshot_health
                  ->  snapshot_diff
                  ->  activity_features
Across all snapshots:
    activity_features  ->  slip_models  ->  slip_predictions
    quality_report  (with asset checks in checks.py)

Every asset is idempotent: it deletes and rewrites its own rows, so retries and backfills
never duplicate anything.
"""

from dagster import (
    AssetExecutionContext,
    Backoff,
    MaterializeResult,
    MetadataValue,
    RetryPolicy,
    asset,
)

from baseline.analytics.cpm import store_cpm
from baseline.analytics.critical_path import driving_path
from baseline.analytics.dcma import health_score, run_dcma
from baseline.analytics.diff import diff_schedules
from baseline.db.reader import list_projects, read_schedule, snapshot_on_or_before
from baseline.db.session import tenant_connection
from baseline.db.writer import current_snapshots
from baseline.ml.embed_notes import embed_snapshot_notes
from baseline.ml.features import compute_features, store_features
from baseline.ml.predict import predict
from baseline.ml.train import train
from baseline.orchestration.partitions import parse_partition_key, snapshot_partitions
from baseline.orchestration.resources import BaselineResource
from baseline.quality.checks import run_all

# Database blips and deadlocks are worth retrying; bad data is not (it gets quarantined).
RETRY = RetryPolicy(max_retries=3, delay=10, backoff=Backoff.EXPONENTIAL)


def _snapshot(conn, project_id, data_date):
    snap = snapshot_on_or_before(conn, project_id, data_date)
    if not snap or snap["data_date"] != data_date:
        raise ValueError(f"no snapshot for {project_id} on {data_date}")
    return snap


@asset(partitions_def=snapshot_partitions, retry_policy=RETRY, group_name="per_snapshot")
def snapshot_cpm(context: AssetExecutionContext) -> MaterializeResult:
    """Critical path and float for one snapshot, plus delay-note vectors."""
    tenant, project, day = parse_partition_key(context.partition_key)
    with tenant_connection(tenant) as conn:
        snap = _snapshot(conn, project, day)
        schedule = read_schedule(conn, snap["snapshot_id"])
        cpm = store_cpm(conn, tenant, snap["snapshot_id"], schedule)
        notes = embed_snapshot_notes(conn, tenant, snap["snapshot_id"])
    critical = sum(r.is_critical for r in cpm.activities.values())
    return MaterializeResult(
        metadata={
            "forecast_finish": str(cpm.project_finish),
            "critical_activities": critical,
            "driving_path": MetadataValue.text(" -> ".join(driving_path(schedule, cpm))),
            "delay_notes_embedded": notes,
        }
    )


@asset(partitions_def=snapshot_partitions, deps=[snapshot_cpm], group_name="per_snapshot")
def snapshot_health(context: AssetExecutionContext) -> MaterializeResult:
    """DCMA 14-point assessment for one snapshot."""
    tenant, project, day = parse_partition_key(context.partition_key)
    with tenant_connection(tenant) as conn:
        schedule = read_schedule(conn, _snapshot(conn, project, day)["snapshot_id"])
    checks = run_dcma(schedule)
    table = "| # | Check | Value | Result |\n|---|---|---|---|\n" + "\n".join(
        f"| {c.number} | {c.name} | {c.display_value} | "
        f"{ {True: 'pass', False: 'FAIL', None: 'n/a'}[c.passed] } |"
        for c in checks
    )
    return MaterializeResult(
        metadata={
            "health_score": round(health_score(checks), 1),
            "failed_checks": [c.name for c in checks if c.passed is False],
            "report": MetadataValue.md(table),
        }
    )


@asset(partitions_def=snapshot_partitions, deps=[snapshot_cpm], group_name="per_snapshot")
def snapshot_diff(context: AssetExecutionContext) -> MaterializeResult:
    """What changed since the previous update."""
    tenant, project, day = parse_partition_key(context.partition_key)
    with tenant_connection(tenant) as conn:
        snaps = [s for s in current_snapshots(conn, project) if s["data_date"] <= day]
        if len(snaps) < 2:
            return MaterializeResult(metadata={"note": "first update; nothing to compare"})
        old = read_schedule(conn, snaps[-2]["snapshot_id"])
        new = read_schedule(conn, snaps[-1]["snapshot_id"])
    d = diff_schedules(old, new)
    return MaterializeResult(
        metadata={
            "finish_slip_days": d.finish_slip_days,
            "float_eroded": len(d.float_erosion),
            "entered_critical": len(d.entered_critical),
            "milestones_moved": len(d.milestone_moves),
            "summary": MetadataValue.md("\n".join(f"- {line}" for line in d.summary_lines())),
        }
    )


@asset(partitions_def=snapshot_partitions, deps=[snapshot_cpm], group_name="per_snapshot")
def activity_features(context: AssetExecutionContext) -> MaterializeResult:
    """ML features for every incomplete activity, as of this snapshot's data date."""
    tenant, project, day = parse_partition_key(context.partition_key)
    with tenant_connection(tenant) as conn:
        rows = store_features(conn, tenant, project, day, compute_features(conn, project, day))
    return MaterializeResult(metadata={"rows": rows})


@asset(deps=[activity_features], group_name="models")
def slip_models(context: AssetExecutionContext, baseline: BaselineResource) -> MaterializeResult:
    """One slip model per tenant, trained only on that tenant's history."""
    reports = {}
    for tenant in baseline.tenant_list:
        with tenant_connection(tenant) as conn:
            try:
                reports[tenant] = vars(train(conn, tenant))
            except ValueError as exc:  # not enough history yet
                context.log.warning(f"{tenant}: {exc}")
    return MaterializeResult(metadata={"reports": MetadataValue.json(reports)})


@asset(deps=[slip_models], group_name="models")
def slip_predictions(
    context: AssetExecutionContext, baseline: BaselineResource
) -> MaterializeResult:
    """Score the latest update of every project."""
    top = {}
    for tenant in baseline.tenant_list:
        with tenant_connection(tenant) as conn:
            for project in list_projects(conn):
                try:
                    df = predict(conn, tenant, project)
                except (FileNotFoundError, ValueError) as exc:
                    context.log.warning(f"{tenant}/{project}: {exc}")
                    continue
                top[f"{tenant}/{project}"] = df.head(5)[
                    ["activity_id", "name", "slip_probability"]
                ].to_dict("records")
    return MaterializeResult(metadata={"top_risks": MetadataValue.json(top)})


@asset(group_name="quality")
def quality_report(baseline: BaselineResource) -> MaterializeResult:
    """Freshness, volume, integrity, and quarantine rate for every project."""
    results = {}
    for tenant in baseline.tenant_list:
        with tenant_connection(tenant) as conn:
            for project in list_projects(conn):
                results[f"{tenant}/{project}"] = {
                    c.name: c.passed for c in run_all(conn, project)
                }
    return MaterializeResult(metadata={"results": MetadataValue.json(results)})