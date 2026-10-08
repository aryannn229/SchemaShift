"""Shared helpers for codegen tests."""

import json
from pathlib import Path
from typing import Any

from schemashift.codegen import generate_code
from schemashift.codegen.layout import TableLayout, build_layouts
from schemashift.codegen.queries import QueryContext, TranslatedQuery, translate_all
from schemashift.models import Placement, PlacementPlan
from schemashift.pipeline import CompileOptions, CompileResult, compile_sql

SAMPLES = Path(__file__).resolve().parents[4] / "samples"
SAMPLE_NAMES = ["ecommerce", "blog", "university", "banking"]


def compile_with_plan(
    sql: str,
    queries: str = "",
    plan: dict[str, Placement] | None = None,
    options: CompileOptions | None = None,
) -> tuple[CompileResult, dict[str, TableLayout]]:
    """Compile and then replace the optimizer plan by an explicit one (for layout tests)."""
    result = compile_sql(sql, queries, "", options)
    chosen = PlacementPlan.of(plan or {})
    return result, build_layouts(result.graph, chosen)


def translate(
    sql: str,
    queries: str,
    plan: dict[str, Placement] | None = None,
    options: CompileOptions | None = None,
) -> list[TranslatedQuery]:
    result, layouts = compile_with_plan(sql, queries, plan, options)
    ctx = QueryContext(result.schema_, result.graph, layouts)
    return translate_all(result.queries, ctx)


def sample(name: str) -> tuple[str, str, CompileOptions]:
    folder = SAMPLES / name
    opts_file = folder / "options.json"
    options = (
        CompileOptions.model_validate(json.loads(opts_file.read_text(encoding="utf-8")))
        if opts_file.exists()
        else CompileOptions()
    )
    return (
        (folder / "schema.sql").read_text(encoding="utf-8"),
        (folder / "queries.sql").read_text(encoding="utf-8"),
        options,
    )


def generate_sample(name: str) -> Any:
    schema_sql, queries_sql, options = sample(name)
    result = compile_sql(schema_sql, queries_sql, "", options)
    return result, generate_code(
        result, options, run_id="snapshot", timestamp="2026-01-01T00:00:00Z"
    )
