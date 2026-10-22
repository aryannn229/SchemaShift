import { ArrowRight } from "lucide-react";

import { useMetrics } from "../api/hooks";
import { Badge, Card, CardHeader, SkeletonBlock, StatCard } from "../components/ui";

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
const obj = (x: unknown): Obj => (x && typeof x === "object" ? (x as Obj) : {});
const pct = (x: unknown) => (typeof x === "number" ? `${(x * 100).toFixed(1)}%` : "n/a");
const num = (x: unknown) => (typeof x === "number" ? String(x) : "n/a");
const list = (x: unknown): unknown[] => (Array.isArray(x) ? x : []);

function GroupCard({ title, group, pending }: { title: string; group: Obj; pending: string }) {
  const schemas = list(group.schemas).length;
  const problem = obj(group.problem_detection);
  return (
    <Card>
      <CardHeader
        title={title}
        actions={<Badge value={schemas ? `${schemas} schemas` : "pending"} tone={schemas ? "neutral" : "changed"} />}
      />
      {schemas === 0 ? (
        <p className="px-4 py-3 text-sm text-muted">{pending}</p>
      ) : (
        <div className="grid grid-cols-2 gap-3 p-4 lg:grid-cols-4">
          <StatCard label="Labeled guarantees" value={num(group.labeled_nodes)} />
          <StatCard label="Problem recall" value={pct(problem.recall)} />
          <StatCard label="Problem precision" value={pct(problem.precision)} />
          <StatCard label="Severity accuracy" value={pct(group.severity_accuracy)} />
        </div>
      )}
    </Card>
  );
}

function Metrics() {
  const m = useMetrics();
  if (m.isPending) return <SkeletonBlock rows={4} label="Loading metrics" />;
  if (m.isError || !m.data.available || !m.data.data)
    return <p className="px-4 py-3 text-sm text-muted">Metrics are not available on this server.</p>;
  const d = m.data.data as Obj;
  const self = obj(d.self_labeled ?? d.equivalence);
  const independent = obj(d.independent);
  const external = obj(d.external);
  const externalRs = obj(external.result_set);
  const sample = obj(d.result_set);
  const agreement = obj(d.agreement);
  const mock = obj(agreement.mock);
  const real = obj(agreement.real);
  const mutation = obj(d.mutation);
  const kappa = (r: Obj) => (typeof r.kappa === "number" ? r.kappa.toFixed(2) : "n/a");
  return (
    <div className="space-y-4 p-4">
      <p className="text-sm text-muted">
        Self-labeled and independently labeled results are reported separately and never merged. The
        self-labeled corpus was written by the author of the rules, so it shows internal consistency,
        not independent validation.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        <GroupCard
          title="Self-labeled corpus (author)"
          group={self}
          pending="No self-labeled schemas."
        />
        <GroupCard
          title="Independent corpus (blind, another person)"
          group={independent}
          pending="No independently labeled schemas yet: this needs a second person, so no independent claim is made."
        />
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Public schemas: queries matching PostgreSQL"
          value={externalRs.computed ? `${num(externalRs.match)} / ${num(externalRs.verified)}` : "n/a"}
          hint="no labels involved"
        />
        <StatCard
          label="Bundled samples: queries matching"
          value={`${num(sample.match)} / ${num(sample.verified)}`}
        />
        <StatCard
          label="Optimizer vs AI (kappa)"
          value={real.pairs ? kappa(real) : "not measured"}
          hint={`mock advisor kappa ${kappa(mock)} (not an AI signal)`}
        />
        <StatCard
          label="Mutation score"
          value={typeof mutation.score === "number" ? pct(mutation.score) : "not measured"}
          hint="equivalence and optimizer"
        />
      </div>
      <p className="text-xs text-muted">
        Computed {typeof d.date === "string" ? d.date : "n/a"} at commit{" "}
        <span className="font-mono">{typeof d.commit === "string" ? d.commit : "n/a"}</span>. Details:
        docs/METRICS.md.
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
