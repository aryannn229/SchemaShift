from pathlib import Path

from typer.testing import CliRunner

from schemashift.cli import app

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


def test_compile_prints_verdict_and_writes_files(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(SAMPLES / "blog" / "schema.sql"),
            "-q",
            str(SAMPLES / "blog" / "queries.sql"),
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "verdict:" in result.output and "EMBED" in result.output
    assert (tmp_path / "mongosh" / "01_collections.js").exists()
    assert (tmp_path / "python" / "migrate.py").exists()
    assert "\r" not in (tmp_path / "python" / "helpers.py").read_text(encoding="utf-8")


def test_compile_fail_on_broken(tmp_path: Path) -> None:
    schema = tmp_path / "s.sql"
    schema.write_text(
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id) ON DELETE CASCADE);"
    )
    opts = tmp_path / "o.json"
    opts.write_text('{"relationship_overrides": {"fk:b.a_id->a.id": "REFERENCE"}}')
    ok = CliRunner().invoke(app, ["compile", str(schema), "-o", str(opts)])
    assert ok.exit_code == 0 and "BROKEN" in ok.output
    failed = CliRunner().invoke(
        app, ["compile", str(schema), "-o", str(opts), "--fail-on", "broken"]
    )
    assert failed.exit_code == 1


def test_compile_reports_errors_with_exit_code_1(tmp_path: Path) -> None:
    schema = tmp_path / "s.sql"
    schema.write_text("CREATE TABLE a (id INT REFERENCES ghost(id));")
    result = CliRunner().invoke(app, ["compile", str(schema)])
    assert result.exit_code == 1 and "SEM001" in result.output
