from typer.testing import CliRunner

from schemashift import __version__
from schemashift.cli import app


def test_version_command() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output
