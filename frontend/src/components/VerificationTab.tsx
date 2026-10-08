import type { RunDetail } from "../api/client";
import { Badge } from "./Badge";
import { Empty, ErrorBox, Loading } from "./States";

export function VerificationTab({ run }: { run?: RunDetail }) {
  if (!run) return <Empty>Use Compile &amp; Verify to run the queries on real databases.</Empty>;
  if (run.status === "pending" || run.status === "running") return <Loading label="Verifying…" />;
  if (run.status === "failed") return <ErrorBox message={run.error ?? "Run failed"} />;
  const v = run.verification;
  if (!v) return <Empty>This run was not verified.</Empty>;
  return (
    <div className="space-y-3 p-4 text-sm">
      {v.queries.map((q) => (
        <details key={q.query_id} className="rounded border p-2 dark:border-slate-700">
          <summary className="cursor-pointer">
            <Badge value={q.status} /> <b>{q.query_id}</b>{" "}
            <span className="font-mono text-xs">{q.sql}</span>
          </summary>
          <p className="mt-1">
            PostgreSQL rows: {q.pg_rows} · MongoDB rows: {q.mongo_rows}
          </p>
          {q.hypothesis && <p className="mt-1 italic">{q.hypothesis}</p>}
          {q.error && <ErrorBox message={q.error} />}
          {q.differing && q.differing.length > 0 && (
            <pre className="mt-1 overflow-auto text-xs">{JSON.stringify(q.differing, null, 2)}</pre>
          )}
        </details>
      ))}
      <h3 className="font-semibold">Probes</h3>
      {(v.probes ?? []).length === 0 && <p className="text-slate-500">No probes.</p>}
      {(v.probes ?? []).map((p) => (
        <div key={p.verdict_id} className="rounded border p-2 dark:border-slate-700">
          <Badge value={p.status} /> {p.description}
          <div className="text-xs text-slate-500">
            PG: {p.postgres} · Mongo: {p.mongo} ·{" "}
            {p.demonstrates ? "demonstrated" : "not demonstrated"}
          </div>
        </div>
      ))}
    </div>
  );
}
