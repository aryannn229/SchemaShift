"""Command line entry point."""

from pathlib import Path

import typer

from schemashift import __version__

app = typer.Typer(help="SchemaShift CLI")


@app.command()
def version() -> None:
    """Print the SchemaShift version."""
    typer.echo(__version__)


@app.command()
def compile(  # noqa: A001 (CLI verb)
    schema: Path = typer.Argument(..., exists=True, readable=True, help="PostgreSQL DDL file"),
    queries: Path = typer.Option(None, "--queries", "-q", help="Optional SQL queries file"),
    seed: Path = typer.Option(None, "--seed", help="Optional INSERT seed file"),
    options: Path = typer.Option(None, "--options", "-o", help="JSON file with CompileOptions"),
    out: Path = typer.Option(None, "--out", help="Directory to write the generated code to"),
    fail_on: str = typer.Option("none", help="Exit 1 when the verdict reaches none|changed|broken"),
) -> None:
    """Compile a PostgreSQL schema: verdicts, placement plan and generated MongoDB code."""
    from schemashift.codegen import generate_code
    from schemashift.pipeline import CompileOptions, compile_sql

    opts = (
        CompileOptions.model_validate_json(options.read_text(encoding="utf-8"))
        if options
        else CompileOptions()
    )
    result = compile_sql(
        schema.read_text(encoding="utf-8"),
        queries.read_text(encoding="utf-8") if queries else "",
        seed.read_text(encoding="utf-8") if seed else "",
        opts,
    )
    for d in result.diagnostics:
        where = f"{d.location.line_start}:{d.location.col_start}" if d.location else "-"
        typer.echo(f"{d.severity.upper():7} {d.code:28} {where:>8}  {d.message}")
    counts = result.equivalence.final.counts
    typer.echo(
        f"verdict: {result.overall_verdict}  (SAFE {counts['SAFE']}, CHANGED {counts['CHANGED']}, "
        f"BROKEN {counts['BROKEN']})"
    )
    for rid, dec in sorted(result.plan.decisions.items()):
        typer.echo(f"  {dec.decision:9} {dec.score:.2f}  {rid}")
    if out is not None:
        generated = generate_code(result, opts)
        for f in generated.files:
            target = out / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.content, encoding="utf-8", newline="\n")
        typer.echo(f"wrote {len(generated.files)} file(s) to {out}")
    order = {"none": 99, "changed": 1, "broken": 2}
    rank = {"SAFE": 0, "CHANGED": 1, "BROKEN": 2}[result.overall_verdict]
    raise typer.Exit(1 if result.has_errors or rank >= order.get(fail_on, 99) else 0)


@app.command()
def verify(
    schema: Path = typer.Argument(..., exists=True, readable=True, help="PostgreSQL DDL file"),
    queries: Path = typer.Option(None, "--queries", "-q", help="Optional SQL queries file"),
    seed_file: Path = typer.Option(None, "--seed-file", help="Optional INSERT seed file"),
    options: Path = typer.Option(None, "--options", "-o", help="JSON file with CompileOptions"),
    seed: int = typer.Option(0, help="Seed of the synthetic data generator"),
    rows: int = typer.Option(50, help="Rows per table (max 500)"),
    probes: bool = typer.Option(True, help="Run guarantee probes for CHANGED/BROKEN verdicts"),
    admin_dsn: str = typer.Option(
        None,
        envvar="SANDBOX_ADMIN_DATABASE_URL",
        help="PostgreSQL role allowed to CREATE ROLE/SCHEMA",
    ),
    mongo_url: str = typer.Option(None, envvar="MONGO_URL", help="MongoDB URI (replica set)"),
) -> None:
    """Run the SQL on PostgreSQL and the generated code on MongoDB with identical seed data."""
    from schemashift.pipeline import CompileOptions, compile_sql
    from schemashift.verification import SandboxConfig
    from schemashift.verification import verify as run_verify

    if not admin_dsn or not mongo_url:
        raise typer.BadParameter(
            "set SANDBOX_ADMIN_DATABASE_URL and MONGO_URL (or pass the options)"
        )
    opts = (
        CompileOptions.model_validate_json(options.read_text(encoding="utf-8"))
        if options
        else CompileOptions()
    )
    opts = opts.model_copy(update={"rows_per_table": rows})
    seed_sql = seed_file.read_text(encoding="utf-8") if seed_file else ""
    result = compile_sql(
        schema.read_text(encoding="utf-8"),
        queries.read_text(encoding="utf-8") if queries else "",
        seed_sql,
        opts,
    )
    report = run_verify(
        result,
        opts,
        SandboxConfig(pg_admin_dsn=admin_dsn, mongo_url=mongo_url),
        seed_sql=seed_sql,
        seed=seed,
        run_probes=probes,
    )
    for o in report.queries:
        detail = o.hypothesis or o.error or o.pg_error or ""
        typer.echo(f"{o.query_id:>4} {o.status:<14} {o.kind:<7} {detail}")
    for p in report.probes:
        flag = "demonstrated" if p.demonstrates else "no difference"
        typer.echo(f"probe {p.verdict_id} ({p.status}): {flag} - {p.description}")
    typer.echo(
        f"result-set correctness: {report.correctness:.1%} "
        f"({len(report.verified)} verified, {len(report.unexplained)} unexplained mismatch(es))"
    )
    raise typer.Exit(1 if report.correctness < 0.95 or report.unexplained else 0)


@app.command()
def evaluate(
    corpus: Path = typer.Option(None, help="Corpus directory (default: tests/corpus)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List every mismatch"),
    write_docs: bool = typer.Option(
        False, "--write-docs", help="Update docs/METRICS.md and docs/metrics.json"
    ),
    verify_samples: bool = typer.Option(
        False, "--verify", help="Also measure result-set correctness (needs PostgreSQL + MongoDB)"
    ),
    with_ai: bool = typer.Option(
        False, "--ai", help="Also measure AI agreement (needs ANTHROPIC_API_KEY)"
    ),
) -> None:
    """Evaluate the equivalence checker against the labeled corpus."""
    from schemashift.metrics.evaluate import (
        DEFAULT_CORPUS,
        evaluate_equivalence,
        format_report,
        load_corpus,
    )

    metrics, _ = evaluate_equivalence(load_corpus(corpus or DEFAULT_CORPUS))
    typer.echo(format_report(metrics))
    system = None
    if verify_samples or with_ai:
        import os

        from schemashift.ai import AnthropicAdvisor
        from schemashift.metrics.evaluate import evaluate_system
        from schemashift.verification import SandboxConfig

        sandbox = None
        if verify_samples:
            sandbox = SandboxConfig(
                pg_admin_dsn=os.environ["SANDBOX_ADMIN_DATABASE_URL"],
                mongo_url=os.environ["MONGO_URL"],
            )
        advisor = None
        if with_ai:
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                typer.echo("--ai needs ANTHROPIC_API_KEY", err=True)
                raise typer.Exit(2)
            advisor = AnthropicAdvisor(api_key=key)
        system = evaluate_system(sandbox=sandbox, advisor=advisor)
        rs, ai = system["result_set"], system["ai"]
        if rs["computed"]:
            typer.echo(f"result-set correctness: {rs['rate']:.1%} ({rs['match']}/{rs['verified']})")
        if ai["computed"]:
            typer.echo(f"AI agreement          : {ai['agreement']} over {ai['judged']} placements")
    if write_docs:
        from schemashift.metrics.evaluate import write_metrics_doc, write_metrics_json

        typer.echo(f"wrote {write_metrics_json(metrics, system)}")
        typer.echo(f"wrote {write_metrics_doc(metrics)}")
    if verbose:
        for m in metrics.mismatches:
            typer.echo(f"MISMATCH {m.case} {m.node_id}: label={m.label} predicted={m.predicted}")
    raise typer.Exit(
        0 if metrics.detection_rate >= 0.95 and metrics.false_positive_rate <= 0.05 else 1
    )


@app.callback()
def main() -> None:
    """SchemaShift: SQL to MongoDB migration compiler."""


if __name__ == "__main__":
    app()
