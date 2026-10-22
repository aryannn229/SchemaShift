"""`schemashift migrate plan|dry-run|run|resume|verify|status|rollback|apply`."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import typer

migrate_app = typer.Typer(
    help="Safe migration: plan, dry-run, run, resume, verify, status, rollback."
)

_SCHEMA = typer.Argument(..., exists=True, readable=True, help="PostgreSQL DDL file")
_SOURCE = typer.Option(None, "--source", envvar="SOURCE_DATABASE_URL", help="PostgreSQL DSN")
_TARGET = typer.Option(None, "--target", envvar="MONGO_URL", help="MongoDB URL")
_DATABASE = typer.Option("migrated", "--database", help="Target MongoDB database")
_STATE = typer.Option(
    Path(".schemashift-migration"), "--state-dir", help="Where the manifest lives"
)
_PGSCHEMA = typer.Option("public", "--pg-schema", help="PostgreSQL schema holding the tables")
_OPTIONS = typer.Option(None, "--options", "-o", help="JSON file with CompileOptions")
_BATCH = typer.Option(500, "--batch-size")
_SCRIPT = typer.Argument(..., exists=True, help="evolve.py from `schemashift diff --out`")


def _job(
    schema: Path,
    source: str | None,
    target: str | None,
    database: str,
    state_dir: Path,
    pg_schema: str,
    options: Path | None,
    batch_size: int,
    prevalidate: bool = True,
) -> Any:
    from schemashift.migrate import Job
    from schemashift.pipeline import CompileOptions, compile_sql

    if not source or not target:
        typer.echo("--source (PostgreSQL DSN) and --target (MongoDB URL) are required", err=True)
        raise typer.Exit(2)
    opts = (
        CompileOptions.model_validate_json(options.read_text(encoding="utf-8"))
        if options
        else CompileOptions()
    )
    result = compile_sql(schema.read_text(encoding="utf-8"), "", "", opts)
    if result.has_errors:
        typer.echo("the schema has errors; fix them first (schemashift compile)", err=True)
        raise typer.Exit(2)
    return Job(
        result, opts, source, target, database, state_dir, pg_schema, batch_size, prevalidate
    )


def _load_script(path: Path) -> dict[str, Any]:
    mod = types.ModuleType("evolve_generated")
    sys.modules["evolve_generated"] = mod
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    exec(code, mod.__dict__)  # noqa: S102 - the user's own generated script
    return mod.__dict__


@migrate_app.command("plan")
def plan(
    schema: Path = _SCHEMA,
    source: str = _SOURCE,
    target: str = _TARGET,
    database: str = _DATABASE,
    state_dir: Path = _STATE,
    pg_schema: str = _PGSCHEMA,
    options: Path = _OPTIONS,
    batch_size: int = _BATCH,
) -> None:
    """Show what a run would do, in order."""
    from schemashift.migrate import plan_preview

    with _job(schema, source, target, database, state_dir, pg_schema, options, batch_size) as job:
        typer.echo(json.dumps(plan_preview(job), indent=2))


@migrate_app.command("dry-run")
def dry_run_cmd(
    schema: Path = _SCHEMA,
    source: str = _SOURCE,
    target: str = _TARGET,
    database: str = _DATABASE,
    state_dir: Path = _STATE,
    pg_schema: str = _PGSCHEMA,
    options: Path = _OPTIONS,
    batch_size: int = _BATCH,
) -> None:
    """Report counts, estimated time and storage and the records that would fail. Writes nothing."""
    from schemashift.migrate import dry_run

    with _job(schema, source, target, database, state_dir, pg_schema, options, batch_size) as job:
        rep = dry_run(job)
    typer.echo(f"source rows      : {rep.tables}")
    typer.echo(f"documents        : {rep.documents}")
    typer.echo(
        f"avg document     : {rep.avg_document_bytes} bytes; "
        f"estimated storage {rep.estimated_storage_bytes} bytes"
    )
    typer.echo(
        f"estimated time   : {rep.estimated_seconds}s at {rep.docs_per_second} docs/s (sampled)"
    )
    typer.echo(
        f"would fail       : {rep.invalid_in_sample} in the sample, "
        f"about {rep.invalid_total_estimate} in total"
    )
    for p in rep.problems[:20]:
        typer.echo(f"  {p['collection']} {p['key']}: {'; '.join(p['reasons'])}")
    typer.echo("writes performed : 0")
    raise typer.Exit(1 if rep.invalid_in_sample else 0)


def _finish(m: Any) -> None:
    tables = ", ".join(f"{t.collection}={t.written}" for t in m.tables)
    typer.echo(f"{m.state}: {tables}; quarantined {m.quarantine_total}")


@migrate_app.command("run")
def run_cmd(
    schema: Path = _SCHEMA,
    source: str = _SOURCE,
    target: str = _TARGET,
    database: str = _DATABASE,
    state_dir: Path = _STATE,
    pg_schema: str = _PGSCHEMA,
    options: Path = _OPTIONS,
    batch_size: int = _BATCH,
    no_validate: bool = typer.Option(
        False, "--no-prevalidate", help="skip server-side validator checks"
    ),
) -> None:
    """Run the migration (resumes automatically when a manifest exists in --state-dir)."""
    from schemashift.migrate import run

    with _job(
        schema, source, target, database, state_dir, pg_schema, options, batch_size, not no_validate
    ) as job:
        _finish(run(job))


@migrate_app.command("resume")
def resume_cmd(
    schema: Path = _SCHEMA,
    source: str = _SOURCE,
    target: str = _TARGET,
    database: str = _DATABASE,
    state_dir: Path = _STATE,
    pg_schema: str = _PGSCHEMA,
    options: Path = _OPTIONS,
    batch_size: int = _BATCH,
) -> None:
    """Continue an interrupted migration from its last checkpoint."""
    from schemashift.migrate import load, run

    if load(state_dir) is None:
        typer.echo(f"no manifest in {state_dir}: nothing to resume (use `migrate run`)", err=True)
        raise typer.Exit(2)
    with _job(schema, source, target, database, state_dir, pg_schema, options, batch_size) as job:
        _finish(run(job))


@migrate_app.command("verify")
def verify_cmd(
    schema: Path = _SCHEMA,
    source: str = _SOURCE,
    target: str = _TARGET,
    database: str = _DATABASE,
    state_dir: Path = _STATE,
    pg_schema: str = _PGSCHEMA,
    options: Path = _OPTIONS,
    sample: int = typer.Option(50, "--sample", help="rows rebuilt and compared per table"),
) -> None:
    """Consistency checks: counts, keys, references, embedded rows and a sampled deep comparison."""
    from schemashift.migrate import verify

    with _job(schema, source, target, database, state_dir, pg_schema, options, 500) as job:
        checks = verify(job, sample=sample)
    for c in checks:
        typer.echo(
            f"{'PASS' if c.ok else 'FAIL'}  {c.name}  expected={c.expected} "
            f"actual={c.actual}  {c.detail}"
        )
    raise typer.Exit(0 if all(c.ok for c in checks) else 1)


@migrate_app.command("status")
def status_cmd(state_dir: Path = _STATE) -> None:
    """Show the manifest of the last run."""
    from schemashift.migrate import status

    typer.echo(json.dumps(status(state_dir), indent=2))


@migrate_app.command("apply")
def apply_cmd(
    script: Path = _SCRIPT,
    target: str = _TARGET,
    database: str = _DATABASE,
    state_dir: Path = _STATE,
    i_understand: bool = typer.Option(False, "--i-understand-this-is-lossy"),
) -> None:
    """Run (or resume) an incremental migration script with a manifest and snapshots."""
    from pymongo import MongoClient

    from schemashift.migrate import LossyStepsNeedConsent, run_script

    ns = _load_script(script)
    client: MongoClient[Any]
    with MongoClient(target) as client:
        try:
            m = run_script(
                client[database],
                ns,
                state_dir,
                database=database,
                consent_lossy=i_understand,
                log=typer.echo,
            )
        except LossyStepsNeedConsent as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(3) from exc
    typer.echo(m.state)


@migrate_app.command("rollback")
def rollback_cmd(
    script: Path = _SCRIPT,
    target: str = _TARGET,
    database: str = _DATABASE,
    state_dir: Path = _STATE,
    i_understand: bool = typer.Option(False, "--i-understand-this-is-lossy"),
) -> None:
    """Undo an incremental migration: reversible steps by rollback, irreversible ones from
    snapshots."""
    from pymongo import MongoClient

    from schemashift.migrate import LossyStepsNeedConsent, rollback_script

    ns = _load_script(script)
    client: MongoClient[Any]
    with MongoClient(target) as client:
        try:
            m = rollback_script(
                client[database], ns, state_dir, consent_lossy=i_understand, log=typer.echo
            )
        except LossyStepsNeedConsent as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(3) from exc
    typer.echo(m.state)
