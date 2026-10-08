"""Golden snapshot tests: SQL fixtures -> expected Schema JSON.

Regenerate snapshots with ``UPDATE_GOLDEN=1 pytest tests/unit/parser/test_golden.py``.
"""

import json
import os
from pathlib import Path

import pytest

from schemashift.parser import parse

GOLDEN = Path(__file__).parent / "golden"
FIXTURES = sorted(GOLDEN.glob("*.sql"))


def _snapshot(sql: str) -> dict[str, object]:
    result = parse(sql)
    return {
        "schema": result.schema_.model_dump(mode="json"),
        "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics],
    }


def test_at_least_fifteen_fixtures() -> None:
    assert len(FIXTURES) >= 15


@pytest.mark.parametrize("sql_file", FIXTURES, ids=lambda p: p.stem)
def test_golden(sql_file: Path) -> None:
    actual = _snapshot(sql_file.read_text(encoding="utf-8"))
    expected_file = sql_file.with_suffix(".json")
    if os.environ.get("UPDATE_GOLDEN"):
        expected_file.write_text(
            json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    assert expected_file.exists(), f"missing snapshot {expected_file.name}"
    expected = json.loads(expected_file.read_text(encoding="utf-8"))
    assert actual == expected
