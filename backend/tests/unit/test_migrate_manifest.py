"""Manifest persistence: atomic writes and a faithful round trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schemashift.migrate import manifest as mf
from schemashift.migrate import status


def test_round_trip_and_atomic_replace(tmp_path: Path) -> None:
    m = mf.Manifest(tables=[mf.TableProgress(table="t", collection="t", total_rows=10)])
    m.tables[0].cursor = ["7"]
    m.tables[0].written = 7
    mf.save(m, tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]  # no temp file left behind
    back = mf.load(tmp_path)
    assert back is not None and back.tables[0].cursor == ["7"] and back.tables[0].written == 7
    m.tables[0].written = 8
    mf.save(m, tmp_path)
    assert mf.load(tmp_path).tables[0].written == 8  # type: ignore[union-attr]


def test_a_failed_write_keeps_the_previous_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = mf.Manifest(tables=[mf.TableProgress(table="t", collection="t")])
    mf.save(m, tmp_path)
    m.tables[0].written = 99

    def boom(src: str, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mf.os, "replace", boom)
    with pytest.raises(OSError):
        mf.save(m, tmp_path)
    monkeypatch.undo()
    assert mf.load(tmp_path).tables[0].written == 0  # type: ignore[union-attr]
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]


def test_status_summarises_progress_and_checks(tmp_path: Path) -> None:
    assert status(tmp_path) == {"state": "none"}
    m = mf.Manifest(
        state="interrupted",
        tables=[
            mf.TableProgress(table="t", collection="t", total_rows=5, written=3, state="running")
        ],
        checks=[{"name": "document count t", "ok": False}],
    )
    mf.save(m, tmp_path)
    st = status(tmp_path)
    assert st["state"] == "interrupted" and st["tables"]["t"] == "running 3/5 (quarantined 0)"
    assert st["checks"] == ["FAIL document count t"]
    assert json.loads((tmp_path / "manifest.json").read_text())["id"] == m.id
