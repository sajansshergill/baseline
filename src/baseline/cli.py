"""The `baseline` command.

    baseline load sample_data/hyperscale_campus/ --tenant demo_owner
    baseline backfill --tenant demo_owner
    baseline diff --project HYPERSCALE_CAMPUS --from 2026-01-05 --to 2026-03-02
    baseline health --project HYPERSCALE_CAMPUS --date 2026-03-02
    baseline train / predict / search / checks
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import typer

from baseline.analytics.cpm import store_cpm
from baseline.analytics.dcma import health_score, run_dcma
from baseline.analytics.diff import diff_schedules
from baseline.db.reader import list_projects, read_schedule, snapshot_on_or_before
from baseline.db.session import tenant_connection
from baseline.db.writer import current_snapshots
from baseline.ml.embed_notes import embed_snapshot_notes, search_notes
from baseline.ml.features import compute_features, store_features
from baseline.ml.predict import predict as run_predict
from baseline.ml.train import train as run_train
from baseline.pipeline import ingest_path
from baseline.quality.checks import run_all

app = typer.Typer(help="Baseline: schedule intelligence for data-center construction.",
                  no_args_is_help=True)  # fmt: skip
TENANT = typer.Option("demo_owner", "--tenant", "-t", envvar="BASELINE_TENANT")
PROJECT = typer.Option(..., "--project", "-p", help="Project ID, e.g. HYPERSCALE_CAMPUS")


def _date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _snapshot(conn, project: str, on: date):
    snap = snapshot_on_or_before(conn, project, on)
    if not snap:
        typer.echo(f"No snapshot of {project} on or before {on}.", err=True)
        raise typer.Exit(1)
    return snap


@app.command()
def load(
    path: Path = typer.Argument(..., exists=True, help="A file or a folder of files"),
    tenant: str = TENANT,
    allow_backdated: bool = typer.Option(False, help="Accept updates older than the latest"),
    reprocess: bool = typer.Option(False, help="Re-run files that were already processed"),
):
    """Ingest schedule files: loaded, duplicate, or quarantined."""
    results = ingest_path(tenant, path, allow_backdated=allow_backdated, reprocess=reprocess)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
        line = f"{r.outcome:<11} {r.filename}"
        if r.data_date:
            line += f"  (data date {r.data_date})"
        if r.reason_code:
            line += f"  {r.reason_code}: {r.detail}"
        typer.echo(line)
        for w in r.warnings:
            typer.echo(f"{'':<11} warning: {w}")
    typer.echo("\nSummary: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


@app.command()
def backfill(tenant: str = TENANT, project: str | None = typer.Option(None, "--project", "-p")):
    """Recompute critical path, note vectors, and ML features for every snapshot."""
    with tenant_connection(tenant) as conn:
        projects = [project] if project else list_projects(conn)
    for p in projects:
        with tenant_connection(tenant) as conn:
            snaps = current_snapshots(conn, p)
            for s in snaps:
                schedule = read_schedule(conn, s["snapshot_id"])
                store_cpm(conn, tenant, s["snapshot_id"], schedule)
                embed_snapshot_notes(conn, tenant, s["snapshot_id"])
            for s in snaps:
                store_features(conn, tenant, p, s["data_date"],
                               compute_features(conn, p, s["data_date"]))  # fmt: skip
        typer.echo(f"{p}: {len(snaps)} snapshots backfilled")


@app.command()
def diff(
    project: str = PROJECT,
    from_: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
    tenant: str = TENANT,
):
    """What changed between two updates."""
    with tenant_connection(tenant) as conn:
        old = read_schedule(conn, _snapshot(conn, project, _date(from_))["snapshot_id"])
        new = read_schedule(conn, _snapshot(conn, project, _date(to))["snapshot_id"])
    for line in diff_schedules(old, new).summary_lines():
        typer.echo(line)


@app.command()
def health(
    project: str = PROJECT,
    on: str = typer.Option(None, "--date", help="YYYY-MM-DD (default: latest)"),
    tenant: str = TENANT,
):
    """DCMA 14-point check of one update."""
    with tenant_connection(tenant) as conn:
        snap = _snapshot(conn, project, _date(on) if on else date.max)
        schedule = read_schedule(conn, snap["snapshot_id"])
    checks = run_dcma(schedule)
    typer.echo(f"{project} as of {schedule.data_date}")
    for c in checks:
        status = {True: "PASS", False: "FAIL", None: "N/A "}[c.passed]
        extra = f"  e.g. {', '.join(c.offenders[:5])}" if c.offenders and not c.passed else ""
        typer.echo(f"{c.number:>2}. {status}  {c.name:<26} {c.display_value:>8}  "
                   f"(target {c.threshold}){extra}")  # fmt: skip
    typer.echo(f"\nHealth score: {health_score(checks):.0f}/100")


@app.command()
def train(tenant: str = TENANT):
    """Train this tenant's slip model on its own history."""
    with tenant_connection(tenant) as conn:
        report = run_train(conn, tenant)
    for k, v in vars(report).items():
        typer.echo(f"{k:<16} {v}")


@app.command()
def predict(project: str = PROJECT, tenant: str = TENANT, top: int = 10):
    """Activities most likely to slip in the next few weeks."""
    with tenant_connection(tenant) as conn:
        df = run_predict(conn, tenant, project)
    if df.empty:
        typer.echo("Nothing left to predict: every activity is complete.")
        return
    typer.echo(f"{project} as of {df['as_of_date'].iloc[0]} (model {df['model_version'].iloc[0]})")
    for r in df.head(top).itertuples():
        typer.echo(
            f"{r.slip_probability:6.1%}  {r.activity_id:<6} {r.name}  "
            f"(float {r.total_float_days:g}d, past slips {r.finish_slip_count:g})"
        )


@app.command()
def search(query: str, tenant: str = TENANT, k: int = typer.Option(5, "--k", "-k"),
           project: str | None = typer.Option(None, "--project", "-p")):  # fmt: skip
    """Find past delay notes similar to a description."""
    with tenant_connection(tenant) as conn:
        hits = search_notes(conn, query, k, project)
    for h in hits:
        typer.echo(f"{h['similarity']:.2f}  {h['project_id']}/{h['activity_id']} "
                   f"({h['data_date']}): {h['note']}")  # fmt: skip


@app.command()
def checks(project: str = PROJECT, tenant: str = TENANT,
           today: str = typer.Option(None, help="Pretend today is YYYY-MM-DD")):  # fmt: skip
    """Run the data quality monitors for a project."""
    with tenant_connection(tenant) as conn:
        results = run_all(conn, project, _date(today) if today else None)
    failed = 0
    for c in results:
        failed += not c.passed
        typer.echo(f"{'PASS' if c.passed else 'FAIL'}  {c.name:<22} {c.detail}")
    raise typer.Exit(1 if failed else 0)


if __name__ == "__main__":
    app()