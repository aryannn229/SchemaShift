import { ArrowRight } from "lucide-react";

import { useMetrics } from "../api/hooks";
import { Card, CardHeader, SkeletonBlock, StatCard } from "../components/ui";

export const STAGES = [
  ["Parse", "SQL DDL and queries become typed models (sqlglot, PostgreSQL dialect)."],
  ["Analyze", "Foreign keys form a graph; junction tables and cycles are detected."],
  [
    "IR",
    "Every guarantee SQL gives (keys, constraints, join, NULL and ordering semantics) becomes an IR node.",
  ],
  [
    "Equivalence",
    "Declarative rules decide whether each guarantee is SAFE, CHANGED or BROKEN in MongoDB.",
  ],
  [
    "Optimizer",
    "Weighted features decide EMBED or REFERENCE per relationship; an LLM only offers a second opinion.",
  ],
  ["Codegen", "Validators, indexes, query pipelines, a migration script and enforcement helpers."],
  [
    "Verification",
    "Run the SQL in a sandbox PostgreSQL and the generated code in MongoDB, then diff the results.",
  ],
] as const;

type Obj = Record<string, unknown>;
const pct = (x: unknown) => (typeof x === "number" ? `${(x * 100).toFixed(1)}%` : "n/a");
const num = (x: unknown) => (typeof x === "number" ? String(x) : "n/a");

function Metrics() {
  const m = useMetrics();
  if (m.isPending) return <SkeletonBlock rows={3} label="Loading metrics" />;
  if (m.isError || !m.data.available || !m.data.data)
    return <p className="px-4 py-3 text-sm text-muted">Metrics are not available on this server.</p>;
  const d = m.data.data as Obj;
  const eq = (d.equivalence ?? {}) as Obj;
  const rs = (d.result_set ?? {}) as Obj;
  const independent = d.independent as Obj | undefined;
  return (
    <div className="space-y-3 p-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Corpus schemas" value={num(eq.corpus_schemas)} hint="self-labelled" />
        <StatCard label="Detection rate" value={pct(eq.detection_rate)} hint="self-labelled" />
        <StatCard label="False positive rate" value={pct(eq.false_positive_rate)} hint="self-labelled" />
        <StatCard
          label="Result sets matched"
          value={`${num(rs.match)} / ${num(rs.verified)}`}
          hint="bundled samples"
        />
      </div>
      <p className="text-sm text-muted">
        The figures above come from a corpus whose labels were written by the same author as the
        rules, so they show internal consistency, not independent validation.
        {independent
          ? " Independently labelled results are reported separately."
          : " No independently labelled corpus has been scored yet."}{" "}
        Computed {typeof d.date === "string" ? d.date : "n/a"} at commit{" "}
        <span className="font-mono">{typeof d.commit === "string" ? d.commit : "n/a"}</span>.
      </p>
    </div>
  );
}

export default function About() {
  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div>
        <h2 className="text-2xl font-semibold">How SchemaShift works</h2>
        <p className="mt-2 text-muted">
          SchemaShift is a compiler from a PostgreSQL schema and workload to MongoDB. It reports what
          the migration silently changes, not just how to move the data.
        </p>
      </div>

      <Card>
        <CardHeader title="Pipeline" />
        <ol
          aria-label="Pipeline"
          className="grid gap-px overflow-hidden rounded-b-lg bg-line sm:grid-cols-2"
        >
          {STAGES.map(([name, text], i) => (
            <li key={name} className="flex gap-3 bg-surface p-4">
              <span className="tabular flex h-6 w-6 shrink-0 items-center justify-center rounded bg-raised text-xs font-semibold">
                {i + 1}
              </span>
              <div>
                <div className="flex items-center gap-1 font-medium">
                  {name}
                  {i < STAGES.length - 1 && (
                    <ArrowRight size={12} className="text-muted" aria-hidden />
                  )}
                </div>
                <div className="text-sm text-muted">{text}</div>
              </div>
            </li>
          ))}
        </ol>
      </Card>

      <Card>
        <CardHeader title="Measured results" />
        <Metrics />
      </Card>

      <p className="text-sm text-muted">
        Your SQL is parsed, never executed as text. Verification re-emits the parsed AST into an
        isolated sandbox and uses bound parameters for data.
      </p>
    </div>
  );
}
