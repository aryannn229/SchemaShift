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
