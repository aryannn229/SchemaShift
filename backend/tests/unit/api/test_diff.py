"""POST /api/v1/diff and `schemashift diff`."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from schemashift.cli import app
from tests.unit.api.test_api import SCHEMA, make_client

ALTER = "ALTER TABLE users ADD COLUMN nick TEXT DEFAULT 'x'; ALTER TABLE users RENAME COLUMN email TO mail;"


def test_diff_endpoint_returns_changes_steps_guarantees_and_script(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        res = c.post("/api/v1/diff", json={"old_sql": SCHEMA, "alter_sql": ALTER})
        assert res.status_code == 200
        body = res.json()
        kinds = {op["kind"] for op in body["diff"]["ops"]}
        assert {"ADD_COLUMN", "RENAME_COLUMN"} <= kinds
        assert body["steps"] and all(s["reversibility"] for s in body["steps"])
        assert "def apply(" in body["script"] and "evolve_runtime" in body["script"]
        assert body["guarantees"]["old_overall"] in {"SAFE", "CHANGED", "BROKEN"}
        # an unconfirmed rename stays a candidate
        res = c.post(
            "/api/v1/diff",
            json={"old_sql": SCHEMA, "new_sql": SCHEMA.replace("email", "mail")},
        )
        cands = res.json()["diff"]["rename_candidates"]
        assert any(x["old"] == "email" and not x["confirmed"] for x in cands)
        res = c.post(
            "/api/v1/diff",
            json={
                "old_sql": SCHEMA,
                "new_sql": SCHEMA.replace("email", "mail"),
                "confirmed_renames": {"users.email": "mail"},
            },
        )
        assert "RENAME_COLUMN" in {op["kind"] for op in res.json()["diff"]["ops"]}


def test_diff_endpoint_validation_and_limits(tmp_path: Path) -> None:
    with make_client(tmp_path, max_tables=1) as c:
        assert c.post("/api/v1/diff", json={"old_sql": SCHEMA}).status_code == 422
        assert (
            c.post("/api/v1/diff", json={"old_sql": SCHEMA, "alter_sql": ALTER}).status_code == 422
        )


def test_diff_cli(tmp_path: Path) -> None:
    (tmp_path / "old.sql").write_text(SCHEMA, encoding="utf-8")
    (tmp_path / "alter.sql").write_text(ALTER, encoding="utf-8")
    out = tmp_path / "out"
    r = CliRunner().invoke(
        app,
        [
            "diff",
            str(tmp_path / "old.sql"),
            "--alter",
            str(tmp_path / "alter.sql"),
            "--out",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "ADD_COLUMN" in r.output and "migration step" in r.output
    assert (out / "evolve.py").exists() and (out / "plan.json").exists()
    assert CliRunner().invoke(app, ["diff", str(tmp_path / "old.sql")]).exit_code == 2
