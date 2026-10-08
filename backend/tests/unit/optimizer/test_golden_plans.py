"""Golden placement plans for the four sample schemas.

Regenerate with ``UPDATE_GOLDEN=1 pytest tests/unit/optimizer/test_golden_plans.py``.
"""

import json
import os
from pathlib import Path

import pytest

from schemashift.pipeline import CompileOptions, compile_sql

ROOT = Path(__file__).resolve().parents[4]
SAMPLES = ROOT / "samples"
GOLDEN = Path(__file__).parent / "golden"
NAMES = ["ecommerce", "blog", "university", "banking"]


def plan_snapshot(name: str) -> dict[str, object]:
    folder = SAMPLES / name
    opts_file = folder / "options.json"
    options = (
        CompileOptions.model_validate_json(opts_file.read_text(encoding="utf-8"))
        if opts_file.exists()
        else CompileOptions()
    )
    result = compile_sql(
        (folder / "schema.sql").read_text(encoding="utf-8"),
        (folder / "queries.sql").read_text(encoding="utf-8"),
        "",
        options,
    )
    return {
        "decisions": {
            k: {"decision": d.decision, "score": round(d.score, 2), "host": d.host}
            for k, d in sorted(result.plan.decisions.items())
        },
        "warnings": list(result.plan.warnings),
    }


@pytest.mark.parametrize("name", NAMES)
def test_golden_plan(name: str) -> None:
    actual = plan_snapshot(name)
    target = GOLDEN / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.mkdir(exist_ok=True)
        target.write_text(
            json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    assert json.loads(target.read_text(encoding="utf-8")) == actual


def test_banking_demo_plan_matches_the_demo_story() -> None:
    plan = plan_snapshot("banking")["decisions"]
    assert plan["fk:kyc_profiles.customer_id->customers.id"]["decision"] == "EMBED"  # type: ignore[index]
    assert plan["fk:transactions.account_id->accounts.id"]["decision"] == "REFERENCE"  # type: ignore[index]
    assert plan["m2n:beneficiaries"]["decision"] == "REF_ARRAY"  # type: ignore[index]


def test_samples_compile_without_errors() -> None:
    for name in NAMES:
        folder = SAMPLES / name
        result = compile_sql(
            (folder / "schema.sql").read_text(encoding="utf-8"),
            (folder / "queries.sql").read_text(encoding="utf-8"),
        )
        assert not result.has_errors, (
            name,
            [d for d in result.diagnostics if d.severity == "error"],
        )
