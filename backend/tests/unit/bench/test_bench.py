"""Phase 20: the pure parts of the benchmark lab (statistics, scale parsing, findings, report)."""

from __future__ import annotations

import pytest

from schemashift.bench import parse_scale, to_html, write_report
from schemashift.bench.runner import (
    BenchReport,
    Latency,
    PlanRun,
    QueryBench,
    Storage,
    findings,
    latency,
    percentile,
)


def test_percentile_nearest_rank() -> None:
    xs = [float(i) for i in range(1, 101)]
    assert percentile(xs, 50) == 50.0
    assert percentile(xs, 95) == 95.0
    assert percentile(xs, 99) == 99.0
    assert percentile([], 50) == 0.0
    assert percentile([7.0], 99) == 7.0


def test_latency_summary_and_throughput() -> None:
    lat = latency([0.001, 0.002, 0.003, 0.004])
    assert lat.reps == 4
    assert lat.p50_ms == 2.0
    assert lat.mean_ms == 2.5
    assert lat.throughput_qps == 400.0


@pytest.mark.parametrize(
    ("text", "value"),
    [("1k", 1000), ("100k", 100_000), ("1M", 1_000_000), ("2500", 2500), ("1_000", 1000)],
)
def test_parse_scale(text: str, value: int) -> None:
    assert parse_scale(text) == value


def _lat(p50: float) -> Latency:
    return Latency(
        p50_ms=p50, p95_ms=p50, p99_ms=p50, mean_ms=p50, throughput_qps=1000 / p50, reps=5
    )


def _plan(name: str, p50: float | None, status: str = "MATCH") -> PlanRun:
    q = QueryBench(
        query_id="q1",
        sql="SELECT 1",
        status=status,
        postgres=_lat(1.0) if p50 else None,
        mongo=_lat(p50) if p50 else None,
    )
    storage = Storage(
        postgres_data_bytes=1,
        postgres_index_bytes=1,
        mongo_data_bytes=1,
        mongo_storage_bytes=1,
        mongo_index_bytes=1,
    )
    return PlanRun(
        name=name,
        decisions={"fk:a.b->c.d": "EMBED"},
        collections=["c"],
        load_seconds=1.0,
        queries=[q],
        storage=storage,
        correctness=1.0 if status == "MATCH" else 0.0,
    )


def test_findings_say_when_the_chosen_plan_lost() -> None:
    plans = [_plan("chosen", 5.0), _plan("all_reference", 9.0), _plan("all_embed", 2.0)]
    (line,) = findings(plans)
    assert "all_embed beat the chosen plan" in line
    assert "PostgreSQL 1.00 ms" in line
    win = findings([_plan("chosen", 1.5), _plan("all_reference", 9.0)])
    assert "chosen plan is the fastest" in win[0]


def test_findings_report_untimed_queries() -> None:
    (line,) = findings([_plan("chosen", None, "MISMATCH")])
    assert "not timed" in line and "MISMATCH" in line


def test_report_is_self_contained_html_and_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = BenchReport(
        meta={"scale_rows_per_table": 1000, "postgres": "PostgreSQL 16"},
        plans=[_plan("chosen", 4.0), _plan("all_embed", 2.0)],
        findings=["q1: x <b>escaped</b>"],
    )
    page = to_html(report)
    assert "<svg" in page and "src=" not in page and "http" not in page
    assert "&lt;b&gt;escaped" in page
    assert "1. Correctness" in page and "2. Performance" in page
    js, html_path = write_report(report, tmp_path)
    assert BenchReport.model_validate_json(js.read_text("utf-8")) == report
    assert html_path.read_text("utf-8") == page
