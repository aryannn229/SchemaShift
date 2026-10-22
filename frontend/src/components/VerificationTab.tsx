import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";

import type { RunDetail } from "../api/client";
import { Badge, Card, CardHeader, EmptyState, ErrorState, SkeletonBlock, cx } from "./ui";

const STEPS = ["pending", "running", "done"] as const;

function Timeline({ status }: { status: RunDetail["status"] }) {
  const failed = status === "failed";
  const at = failed ? 1 : STEPS.indexOf(status as (typeof STEPS)[number]);
  return (
    <ol aria-label="Run status" className="flex items-center gap-3 px-4 py-3 text-sm">
      {STEPS.map((s, i) => {
        const state = failed && i === at ? "failed" : i < at || status === "done" ? "done" : i === at ? "active" : "todo";
        const Icon =
          state === "failed" ? XCircle : state === "done" ? CheckCircle2 : state === "active" ? Loader2 : Circle;
        return (
          <li
            key={s}
            aria-current={state === "active" ? "step" : undefined}
            className={cx(
              "flex items-center gap-1.5 capitalize",
              state === "done" && "text-safe",
              state === "failed" && "text-broken",
              state === "todo" && "text-muted",
            )}
          >
            <Icon size={14} aria-hidden className={state === "active" ? "animate-spin" : undefined} />
            {s}
          </li>
        );
      })}
    </ol>
  );
}

export function VerificationTab({ run }: { run?: RunDetail }) {
  if (!run)
    return (
      <EmptyState title="Not verified yet">
        Use Run verification to execute the queries on real PostgreSQL and MongoDB sandboxes.
      </EmptyState>
    );
  if (run.status === "pending" || run.status === "running")
    return (
      <div>
        <Timeline status={run.status} />
        <SkeletonBlock label="Verifying" />
      </div>
    );
  if (run.status === "failed")
    return (
      <div>
        <Timeline status="failed" />
        <ErrorState message={run.error ?? "Run failed"} title="Verification failed" />
      </div>
    );
  const v = run.verification;
  if (!v) return <EmptyState title="This run was not verified" />;
  return (
    <div className="space-y-4 p-4 text-sm">
      <Card>
        <CardHeader title="Run" />
        <Timeline status="done" />
      </Card>
      <Card>
        <CardHeader title="Query results" />
        <div className="divide-y divide-line">
          {v.queries.map((q) => (
            <details key={q.query_id} className="px-4 py-2">
              <summary className="flex cursor-pointer items-center gap-2">
                <Badge value={q.status} />
                <b>{q.query_id}</b>
                <span className="truncate font-mono text-xs text-muted">{q.sql}</span>
              </summary>
              <div className="mt-2 space-y-1">
                <p className="tabular text-muted">
                  PostgreSQL rows: {q.pg_rows} · MongoDB rows: {q.mongo_rows}
                </p>
                {q.hypothesis && <p className="italic">{q.hypothesis}</p>}
                {q.error && <ErrorState message={q.error} title="Query error" />}
                {q.differing && q.differing.length > 0 && (
                  <pre className="overflow-auto rounded border border-line bg-bg p-2 font-mono text-xs">
                    {JSON.stringify(q.differing, null, 2)}
                  </pre>
                )}
              </div>
            </details>
          ))}
        </div>
      </Card>
      <Card>
        <CardHeader title="Guarantee probes" />
        {(v.probes ?? []).length === 0 ? (
          <p className="px-4 py-3 text-muted">No probes.</p>
        ) : (
          <div className="divide-y divide-line">
            {(v.probes ?? []).map((p) => (
              <div key={p.verdict_id} className="px-4 py-2">
                <div className="flex items-center gap-2">
                  <Badge value={p.status} /> {p.description}
                </div>
                <div className="mt-1 text-xs text-muted">
                  PG: {p.postgres} · Mongo: {p.mongo} · {p.demonstrates ? "demonstrated" : "not demonstrated"}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
