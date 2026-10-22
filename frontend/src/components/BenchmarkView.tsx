import { Badge, Card, CardHeader } from "./ui";

export interface Latency {
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  throughput_qps: number;
}
export interface BenchQuery {
  query_id: string;
  sql: string;
  status: string;
  detail?: string | null;
  postgres?: Latency | null;
  mongo?: Latency | null;
  mongo_index_used?: boolean | null;
  rewrites?: string[];
}
export interface BenchPlan {
  name: string;
  correctness: number;
  load_seconds: number;
  queries: BenchQuery[];
  storage: Record<string, number>;
}
export interface BenchResults {
  meta: Record<string, unknown>;
  plans: BenchPlan[];
  findings: string[];
}

/** Narrow unknown JSON to a `schemashift bench` results file, or null when it is something else. */
export function parseBench(raw: unknown): BenchResults | null {
  const r = raw as Partial<BenchResults> | null;
  if (!r || !Array.isArray(r.plans) || !Array.isArray(r.findings) || typeof r.meta !== "object") return null;
  if (!r.plans.every((p) => Array.isArray(p?.queries) && typeof p?.name === "string")) return null;
  return r as BenchResults;
}

const COLORS: Record<string, string> = {
  postgres: "rgb(var(--muted))",
  chosen: "rgb(var(--accent))",
  all_reference: "#d97706",
  all_embed: "#059669",
};
const LABELS: Record<string, string> = {
  postgres: "PostgreSQL",
  chosen: "MongoDB: chosen plan",
  all_reference: "MongoDB: all REFERENCE",
  all_embed: "MongoDB: all EMBED where legal",
};
const mb = (n: number) => `${(n / 1_048_576).toFixed(2)} MB`;

function Bars({
  title,
  rows,
  unit,
}: {
  title: string;
  rows: Record<string, Record<string, number>>;
  unit: string;
}) {
  const peak = Math.max(1e-9, ...Object.values(rows).flatMap((r) => Object.values(r)));
  return (
    <figure aria-label={title} className="space-y-2">
      <figcaption className="text-sm font-medium">{title}</figcaption>
      {Object.entries(rows).map(([qid, series]) => (
        <div key={qid} className="space-y-0.5">
          <div className="font-mono text-xs text-muted">{qid}</div>
          {Object.entries(series).map(([label, v]) => (
            <div key={label} className="flex items-center gap-2 text-xs">
              <span className="w-44 shrink-0 truncate text-muted">{LABELS[label] ?? label}</span>
              <span
                className="h-3 rounded-sm"
                style={{ width: `${Math.max(1, (v / peak) * 60)}%`, background: COLORS[label] ?? "gray" }}
              />
              <span className="tabular">
                {v.toFixed(2)} {unit}
              </span>
            </div>
          ))}
        </div>
      ))}
    </figure>
  );
}

export function BenchmarkView({ data }: { data: BenchResults }) {
  const chosen = data.plans.find((p) => p.name === "chosen") ?? data.plans[0];
  const p50: Record<string, Record<string, number>> = {};
  const qps: Record<string, Record<string, number>> = {};
  for (const q of chosen?.queries ?? []) {
    p50[q.query_id] = {};
    qps[q.query_id] = {};
    if (q.postgres) {
      p50[q.query_id].postgres = q.postgres.p50_ms;
      qps[q.query_id].postgres = q.postgres.throughput_qps;
    }
    for (const plan of data.plans) {
      const m = plan.queries.find((x) => x.query_id === q.query_id)?.mongo;
      if (m) {
        p50[q.query_id][plan.name] = m.p50_ms;
        qps[q.query_id][plan.name] = m.throughput_qps;
      }
    }
  }
  return (
    <div className="space-y-4" data-testid="bench-view">
      <Card>
        <CardHeader title="1. Correctness" />
        <div className="space-y-2 p-4 text-sm">
          <p className="text-muted">
            Checked first with the differential comparison of the verifier; only matching queries are timed.
          </p>
          {data.plans.map((p) => (
            <div key={p.name} className="flex flex-wrap items-center gap-2">
              <span className="w-32 font-mono text-xs">{p.name}</span>
              <Badge
                value={`${Math.round(p.correctness * 100)}% correct`}
                tone={p.correctness === 1 ? "safe" : "changed"}
              />
              {p.queries
                .filter((q) => q.status !== "MATCH")
                .map((q) => (
                  <span key={q.query_id} className="text-xs text-muted">
                    {q.query_id}: {q.status}
                  </span>
                ))}
            </div>
          ))}
        </div>
      </Card>
      <Card>
        <CardHeader title="2. Performance" />
        <div className="space-y-5 p-4">
          <Bars title="Latency p50 (lower is better)" rows={p50} unit="ms" />
          <Bars title="Throughput (higher is better)" rows={qps} unit="qps" />
        </div>
      </Card>
      <Card>
        <CardHeader title="3. Storage" />
        <div className="overflow-x-auto p-4">
          <table className="w-full text-left text-xs tabular">
            <thead>
              <tr>
                <th className="py-1">plan</th>
                <th>PG data</th>
                <th>PG indexes</th>
                <th>Mongo data</th>
                <th>Mongo storage</th>
                <th>Mongo indexes</th>
                <th>load</th>
              </tr>
            </thead>
            <tbody>
              {data.plans.map((p) => (
                <tr key={p.name} className="border-t border-line">
                  <td className="py-1 font-mono">{p.name}</td>
                  <td>{mb(p.storage.postgres_data_bytes ?? 0)}</td>
                  <td>{mb(p.storage.postgres_index_bytes ?? 0)}</td>
                  <td>{mb(p.storage.mongo_data_bytes ?? 0)}</td>
                  <td>{mb(p.storage.mongo_storage_bytes ?? 0)}</td>
                  <td>{mb(p.storage.mongo_index_bytes ?? 0)}</td>
                  <td>{p.load_seconds}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card>
        <CardHeader title="4. Findings" />
        <ul className="list-disc space-y-1 p-4 pl-8 text-sm">
          {data.findings.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      </Card>
      <Card>
        <CardHeader title="5. Machine, versions, parameters" />
        <dl className="grid grid-cols-[12rem_1fr] gap-x-3 gap-y-1 p-4 text-xs">
          {Object.entries(data.meta).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k}</dt>
              <dd className="break-words font-mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </div>
  );
}
