"""Dagster asset checks: data quality monitors that show up as pass/fail in the UI.

Each check runs one monitor from baseline.quality.checks across every tenant and project,
and fails (with the offending projects listed) if any of them fail.
"""

from collections.abc import Callable

from dagster import AssetCheckResult, AssetCheckSeverity, asset_check

from baseline.db.reader import list_projects
from baseline.db.session import tenant_connection
from baseline.orchestration.assets import quality_report
from baseline.orchestration.resources import BaselineResource
from baseline.quality import checks as q


def _run(baseline: BaselineResource, monitor: Callable) -> AssetCheckResult:
    failures = {}
    for tenant in baseline.tenant_list:
        with tenant_connection(tenant) as conn:
            for project in list_projects(conn):
                result = monitor(conn, project)
                if not result.passed:
                    failures[f"{tenant}/{project}"] = result.detail
    return AssetCheckResult(
        passed=not failures,
        severity=AssetCheckSeverity.WARN,
        metadata={"failures": failures} if failures else {"failures": "none"},
    )


@asset_check(asset=quality_report, description="Each project sent an update in the last 10 days")
def freshness_check(baseline: BaselineResource) -> AssetCheckResult:
    return _run(baseline, q.freshness)


@asset_check(asset=quality_report, description="Activity count moved less than 30%")
def volume_check(baseline: BaselineResource) -> AssetCheckResult:
    return _run(baseline, q.volume)


@asset_check(asset=quality_report, description="No relationships point at missing activities")
def referential_integrity_check(baseline: BaselineResource) -> AssetCheckResult:
    return _run(baseline, q.referential_integrity)


@asset_check(asset=quality_report, description="Under 20% of uploads quarantined")
def quarantine_rate_check(baseline: BaselineResource) -> AssetCheckResult:
    return _run(baseline, lambda conn, _project: q.quarantine_rate(conn))


ALL_CHECKS = [freshness_check, volume_check, referential_integrity_check, quarantine_rate_check]