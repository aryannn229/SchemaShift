"""Benchmark laboratory (CLI only): PostgreSQL vs MongoDB under three placement plans."""

from schemashift.bench.report import parse_scale, to_html, write_report
from schemashift.bench.runner import BenchReport, PlanRun, QueryBench, run_bench

__all__ = [
    "BenchReport",
    "PlanRun",
    "QueryBench",
    "parse_scale",
    "run_bench",
    "to_html",
    "write_report",
]
