"""Command line entry point."""

import typer

from schemashift import __version__

app = typer.Typer(help="SchemaShift CLI")


@app.command()
def version() -> None:
    """Print the SchemaShift version."""
    typer.echo(__version__)


@app.callback()
def main() -> None:
    """SchemaShift: SQL to MongoDB migration compiler."""
