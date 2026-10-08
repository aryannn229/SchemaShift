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
def evaluate(
    corpus: Path = typer.Option(None, help="Corpus directory (default: tests/corpus)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List every mismatch"),
    write_docs: bool = typer.Option(False, "--write-docs", help="Update docs/METRICS.md"),
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
    if write_docs:
        from schemashift.metrics.evaluate import write_metrics_doc

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
