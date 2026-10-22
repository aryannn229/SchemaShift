import { AlertCircle, AlertTriangle, Info } from "lucide-react";

import type { CompileResponse, RunDetail } from "../api/client";
import { Card, CardHeader, EmptyState, StatCard, VerdictBadge } from "./ui";

const BANNER: Record<string, string> = {
  SAFE: "border-safe/40 bg-safe-soft text-safe",
  CHANGED: "border-changed/40 bg-changed-soft text-changed",
  BROKEN: "border-broken/40 bg-broken-soft text-broken",
};

const SEVERITY_ICON = { error: AlertCircle, warning: AlertTriangle, info: Info } as const;

export function SummaryTab({ result, run }: { result: CompileResponse; run?: RunDetail }) {
  const total = Object.values(result.counts).reduce((a, b) => a + b, 0);
  const queries = run?.verification?.queries ?? [];
  const matched = queries.filter((q) => q.status === "MATCH").length;
  const findings = result.verdicts
    .filter((v) => v.status !== "SAFE")
    .sort((a, b) => (a.status === b.status ? 0 : a.status === "BROKEN" ? -1 : 1))
    .slice(0, 5);

  return (
    <div className="space-y-4 p-4">
      <div className={`rounded-lg border px-4 py-3 ${BANNER[result.overall_verdict]}`}>
        <div className="text-xs font-medium uppercase tracking-wide opacity-80">Overall verdict</div>
        <div className="text-2xl font-semibold" data-testid="overall-verdict">
          {result.overall_verdict}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Guarantees analysed" value={total} />
        <StatCard label="Safe" value={result.counts.SAFE ?? 0} tone="safe" />
        <StatCard label="Changed" value={result.counts.CHANGED ?? 0} tone="changed" />
        <StatCard label="Broken" value={result.counts.BROKEN ?? 0} tone="broken" />
      </div>
      {(result.concurrency_verdicts ?? []).length > 0 && (
        <StatCard
          label="Concurrency scenarios"
          value={`${result.concurrency_counts?.BROKEN ?? 0} broken, ${result.concurrency_counts?.CHANGED ?? 0} conditional`}
          hint={`${result.concurrency_verdicts?.length} scenarios; ${result.concurrency_counts?.SAFE ?? 0} safe by construction`}
          tone={result.concurrency_overall === "BROKEN" ? "broken" : result.concurrency_overall === "CHANGED" ? "changed" : "safe"}
        />
      )}
      {queries.length > 0 && (
        <StatCard
          label="Queries matched"
          value={`${matched} / ${queries.length}`}
          hint="PostgreSQL vs MongoDB result sets"
          tone={matched === queries.length ? "safe" : "broken"}
        />
      )}

      <Card>
        <CardHeader title="Top findings" />
        {findings.length === 0 ? (
          <EmptyState>No CHANGED or BROKEN guarantees.</EmptyState>
        ) : (
          <ul className="divide-y divide-line text-sm">
            {findings.map((v) => (
              <li key={v.node_id} className="flex items-start gap-3 px-4 py-2">
                <VerdictBadge status={v.status} />
                <div className="min-w-0">
                  <div className="truncate font-mono text-xs text-muted">{v.node_id}</div>
                  <div>{v.reason}</div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader title="Diagnostics" />
        {result.diagnostics.length === 0 ? (
          <p className="px-4 py-3 text-sm text-muted">No diagnostics.</p>
        ) : (
          <ul className="divide-y divide-line text-sm">
            {result.diagnostics.map((d, i) => {
              const Icon = SEVERITY_ICON[d.severity as keyof typeof SEVERITY_ICON] ?? Info;
              return (
                <li key={i} className="flex items-start gap-2 px-4 py-2">
                  <Icon size={14} className="mt-1 shrink-0 text-muted" aria-hidden />
                  <span>
                    <span className="sr-only">{d.severity}: </span>
                    <b className="font-mono text-xs">{d.code}</b> {d.message}
                    {d.location ? ` (line ${d.location.line_start})` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
