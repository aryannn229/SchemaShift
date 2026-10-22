"""Write a benchmark result as JSON and as a self-contained HTML report (inline SVG charts)."""

from __future__ import annotations

import html
from pathlib import Path

from schemashift.bench.runner import BenchReport, PlanRun

COLORS = {
    "postgres": "#6b7280",
    "chosen": "#2563eb",
    "all_reference": "#d97706",
    "all_embed": "#059669",
}
LABELS = {
    "postgres": "PostgreSQL",
    "chosen": "MongoDB: chosen plan",
    "all_reference": "MongoDB: all REFERENCE",
    "all_embed": "MongoDB: all EMBED where legal",
}


def parse_scale(text: str) -> int:
    t = text.strip().lower().replace("_", "")
    mult = {"k": 1_000, "m": 1_000_000}.get(t[-1], 1)
    return int(float(t[:-1] if t[-1] in "km" else t) * mult)


def _bars(title: str, series: dict[str, dict[str, float]], unit: str) -> str:
    """Grouped horizontal bars: ``series[group][label] = value``."""
    peak = max((v for g in series.values() for v in g.values()), default=0) or 1
    row_h, left, width = 18, 150, 640
    y = 28
    parts = [f'<text x="0" y="16" class="t">{html.escape(title)}</text>']
    for group, values in series.items():
        parts.append(f'<text x="0" y="{y + 12}" class="g">{html.escape(group)}</text>')
        for label, v in values.items():
            w = max(1.0, v / peak * (width - left - 70))
            parts.append(
                f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{row_h - 4}" '
                f'fill="{COLORS.get(label, "#888")}"><title>{html.escape(LABELS.get(label, label))}'
                f": {v:.3f} {unit}</title></rect>"
                f'<text x="{left + w + 6:.1f}" y="{y + 11}" class="v">{v:.2f} {unit}</text>'
            )
            y += row_h
        y += 8
    legend = "".join(
        f'<span><i style="background:{COLORS[k]}"></i>{html.escape(v)}</span>'
        for k, v in LABELS.items()
    )
    return (
        f'<figure><svg viewBox="0 0 {width} {y}" role="img" aria-label="{html.escape(title)}">'
        + "".join(parts)
        + f"</svg><figcaption class='legend'>{legend}</figcaption></figure>"
    )


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.2f} MB"


def to_html(report: BenchReport) -> str:
    plans = {p.name: p for p in report.plans}
    chosen = plans.get("chosen")
    qids = [q.query_id for q in chosen.queries] if chosen else []

    def series(metric: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for qid in qids:
            row: dict[str, float] = {}
            cq = next(q for q in chosen.queries if q.query_id == qid) if chosen else None
            if cq and cq.postgres:
                row["postgres"] = getattr(cq.postgres, metric)
            for name, run in plans.items():
                q = next((x for x in run.queries if x.query_id == qid), None)
                if q and q.mongo:
                    row[name] = getattr(q.mongo, metric)
            if row:
                out[qid] = row
        return out

    def correctness_rows(run: PlanRun) -> str:
        return "".join(
            f"<tr><td>{html.escape(q.query_id)}</td><td>{html.escape(q.status)}</td>"
            f"<td>{html.escape(q.detail or '')}</td>"
            f"<td>{html.escape(', '.join(q.rewrites))}</td></tr>"
            for q in run.queries
        )

    storage = "".join(
        f"<tr><td>{html.escape(LABELS.get(n, n))}</td><td>{_mb(p.storage.postgres_data_bytes)}</td>"
        f"<td>{_mb(p.storage.postgres_index_bytes)}</td><td>{_mb(p.storage.mongo_data_bytes)}</td>"
        f"<td>{_mb(p.storage.mongo_storage_bytes)}</td><td>{_mb(p.storage.mongo_index_bytes)}</td>"
        f"<td>{p.load_seconds:.1f} s</td></tr>"
        for n, p in plans.items()
    )
    timing_rows = ""
    for qid in qids:
        for name, run in plans.items():
            q = next((x for x in run.queries if x.query_id == qid), None)
            if q is None or q.mongo is None:
                continue
            pg = q.postgres
            timing_rows += (
                f"<tr><td>{html.escape(qid)}</td><td>{html.escape(name)}</td>"
                f"<td>{q.mongo.p50_ms}</td><td>{q.mongo.p95_ms}</td><td>{q.mongo.p99_ms}</td>"
                f"<td>{q.mongo.throughput_qps}</td><td>{'yes' if q.mongo_index_used else 'no'}</td>"
                f"<td>{q.mongo_docs_examined}</td>"
                f"<td>{pg.p50_ms if pg else ''}</td><td>{pg.p95_ms if pg else ''}</td>"
                f"<td>{'yes' if q.postgres_index_used else 'no'}</td></tr>"
            )
    meta = "".join(
        f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>"
        for k, v in report.meta.items()
    )
    decisions = "".join(
        f"<tr><td>{html.escape(rid)}</td>"
        + "".join(f"<td>{html.escape(p.decisions.get(rid, ''))}</td>" for p in report.plans)
        + "</tr>"
        for rid in (chosen.decisions if chosen else {})
    )
    head_row = "<tr><th>query</th><th>status</th><th>detail</th><th>rewrites</th></tr>"
    sections = "".join(
        f"<h3>{html.escape(p.name)} (correct: {p.correctness:.0%})</h3>"
        f"<table>{head_row}{correctness_rows(p)}</table>"
        for p in report.plans
    )
    heads = "".join(f"<th>{html.escape(p.name)}</th>" for p in report.plans)
    findings = "".join(f"<li>{html.escape(f)}</li>" for f in report.findings)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SchemaShift
benchmark</title>
<style>
body{{font:14px/1.5 system-ui,sans-serif;margin:24px auto;max-width:900px;padding:0
16px;color:#111}}
h1,h2{{margin:1.2em 0 .4em}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #ddd;padding:4px 8px;text-align:left}}th{{background:#f5f5f5}}
svg{{width:100%;height:auto}}.t{{font-weight:600;font-size:13px}}.g{{font-size:12px;fill:#444}}
.v{{font-size:11px;fill:#333}}.legend{{display:flex;flex-wrap:wrap;gap:12px;font-size:12px}}
.legend i{{display:inline-block;width:10px;height:10px;margin-right:4px}}
</style></head><body>
<h1>SchemaShift benchmark</h1>
<p>Correctness is checked first with the verifier's differential comparison; only queries that
return
the same rows are timed. The two are reported separately.</p>
<h2>1. Correctness</h2>
{sections}
<h2>2. Performance</h2>
{_bars("Latency p50 (lower is better)", series("p50_ms"), "ms")}
{_bars("Latency p95", series("p95_ms"), "ms")}
{_bars("Throughput (higher is better)", series("throughput_qps"), "qps")}
<table><tr><th>query</th><th>plan</th><th>p50 ms</th><th>p95 ms</th><th>p99 ms</th><th>qps</th>
<th>index (Mongo)</th><th>docs examined</th><th>PG p50</th><th>PG p95</th><th>index
(PG)</th></tr>{timing_rows}</table>
<h2>3. Storage (data plus indexes)</h2>
<table><tr><th>plan</th><th>PG data</th><th>PG indexes</th><th>Mongo data</th><th>Mongo storage</th>
<th>Mongo indexes</th><th>load time</th></tr>{storage}</table>
<h2>4. Findings</h2><ul>{findings}</ul>
<h2>5. Plans compared</h2><table><tr><th>relationship</th>{heads}</tr>{decisions}</table>
<h2>6. Machine, versions, parameters</h2><table>{meta}</table>
</body></html>"""


def write_report(report: BenchReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "results.json"
    js.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    page = out_dir / "report.html"
    page.write_text(to_html(report), encoding="utf-8")
    return js, page
