import { RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { CompileRequest, CompileResponse } from "../api/client";
import { useSimulate } from "../api/hooks";
import { Badge, Button, Card, CardHeader, ErrorState } from "./ui";

type Choice = "optimizer" | "EMBED" | "REFERENCE";
type Simulation = NonNullable<ReturnType<typeof useSimulate>["data"]>;

/** Constraint toggles derived from the verdicts: each becomes an ALTER statement for the simulation. */
function toggles(result: CompileResponse): { label: string; sql: string }[] {
  const out: { label: string; sql: string }[] = [];
  for (const v of result.verdicts) {
    const m = /^unique:(\w+)\.(\w+)$/.exec(v.node_id);
    if (m)
      out.push({
        label: `Drop UNIQUE on ${m[1]}.${m[2]}`,
        sql: `ALTER TABLE ${m[1]} DROP CONSTRAINT ${m[1]}_${m[2]}_key;`,
      });
    const n = /^not_null:(\w+)\.(\w+)$/.exec(v.node_id);
    if (n && !/\.id$/.test(v.node_id))
      out.push({
        label: `Drop NOT NULL on ${n[1]}.${n[2]}`,
        sql: `ALTER TABLE ${n[1]} ALTER COLUMN ${n[2]} DROP NOT NULL;`,
      });
  }
  return out.slice(0, 6);
}

function Result({ sim }: { sim: Simulation }) {
  const changedPipes = sim.pipelines.filter((p) => p.changed);
  return (
    <div className="space-y-3 text-sm" data-testid="sim-result">
      <div className="flex flex-wrap items-center gap-2">
        <Badge value="simulated plan" tone="accent" />
        <span className="text-xs text-muted">{sim.label}</span>
      </div>
      {sim.blocked.length > 0 && (
        <ul
          className="space-y-1 rounded border border-broken/40 bg-broken-soft p-3 text-broken"
          data-testid="sim-blocked"
        >
          {sim.blocked.map((b) => (
            <li key={b.relationship_id}>
              <b>Blocked:</b> {b.relationship_id} cannot be{" "}
              {b.optimizer === b.simulated ? "changed" : "forced"}.{" "}
              {b.blocked_reason}
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded border border-line p-3" data-testid="sim-risk">
          <div className="text-xs text-muted">Risk score</div>
          <div className="text-xl font-semibold tabular">
            {sim.risk.before} to {sim.risk.after}{" "}
            <span className="text-sm font-normal text-muted">
              ({sim.risk.level_before} to {sim.risk.level_after})
            </span>
          </div>
          <ul className="mt-1 text-xs text-muted tabular">
            {Object.entries(sim.risk.categories)
              .filter(([, [a, b]]) => a !== b)
              .map(([k, [a, b]]) => (
                <li key={k}>
                  {k.replace(/_/g, " ")}: {a} to {b}
                </li>
              ))}
          </ul>
        </div>
        <div
          className="rounded border border-line p-3"
          data-testid="sim-indexes"
        >
          <div className="text-xs text-muted">Indexes and validators</div>
          <ul className="mt-1 space-y-0.5 font-mono text-xs">
            {sim.indexes.added.map((i) => (
              <li key={`+${i}`} className="text-safe">
                + {i}
              </li>
            ))}
            {sim.indexes.removed.map((i) => (
              <li key={`-${i}`} className="text-broken">
                - {i}
              </li>
            ))}
            {sim.validators_changed.map((c) => (
              <li key={`v${c}`}>validator changed: {c}</li>
            ))}
            {sim.indexes.added.length +
              sim.indexes.removed.length +
              sim.validators_changed.length ===
              0 && <li className="text-muted">no change</li>}
          </ul>
        </div>
      </div>
      <div data-testid="sim-verdicts">
        <div className="text-xs text-muted">
          Verdicts: {sim.overall_before} to {sim.overall_after},{" "}
          {sim.verdicts.length} changed
        </div>
        <ul className="mt-1 max-h-48 space-y-0.5 overflow-auto text-xs">
          {sim.verdicts.map((v) => (
            <li key={v.node_id} className="flex flex-wrap gap-2">
              <span className="font-mono">{v.node_id}</span>
              <span>
                {v.before ?? "none"} to {v.after ?? "none"}
              </span>
              {v.evidence_before !== v.evidence_after && (
                <span className="text-muted">
                  evidence {v.evidence_before ?? "none"} to{" "}
                  {v.evidence_after ?? "none"}
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
      {changedPipes.length > 0 && (
        <div data-testid="sim-pipelines">
          <div className="text-xs text-muted">Pipelines that change</div>
          <ul className="mt-1 text-xs">
            {changedPipes.map((p) => (
              <li key={p.query_id}>
                {p.query_id}: {p.collection_before} to {p.collection_after}
                {p.estimated_cost_before != null &&
                  ` (estimated cost ${p.estimated_cost_before} to ${p.estimated_cost_after ?? "n/a"})`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function SimulatorPanel({
  result,
  request,
}: {
  result: CompileResponse;
  request: Pick<CompileRequest, "schema_sql" | "queries_sql" | "options">;
}) {
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [alters, setAlters] = useState<string[]>([]);
  const [freq, setFreq] = useState<Record<string, number>>({});
  const sim = useSimulate();
  const options = useMemo(() => toggles(result), [result]);
  const queryIds = result.queries
    .filter((q) => q.kind === "SELECT")
    .map((q) => q.query_id)
    .slice(0, 4);
  const dirty =
    Object.values(choices).some((c) => c !== "optimizer") ||
    alters.length > 0 ||
    Object.keys(freq).length > 0;

  const { mutate } = sim;
  useEffect(() => {
    if (!dirty) return;
    const overrides = Object.fromEntries(
      Object.entries(choices).filter(([, c]) => c !== "optimizer"),
    ) as Record<string, "EMBED" | "REFERENCE">;
    mutate({
      schema_sql: request.schema_sql,
      queries_sql: request.queries_sql,
      options: request.options ?? {},
      overrides,
      query_freq: freq,
      alter_sql: alters.join("\n"),
    });
  }, [
    choices,
    alters,
    freq,
    dirty,
    mutate,
    request.schema_sql,
    request.queries_sql,
    request.options,
  ]);

  const reset = () => {
    setChoices({});
    setAlters([]);
    setFreq({});
    sim.reset();
  };

  return (
    <Card>
      <CardHeader
        title="Decision simulator"
        actions={
          <Button
            size="sm"
            onClick={reset}
            disabled={!dirty}
            icon={<RotateCcw size={13} aria-hidden />}
          >
            Reset to optimizer plan
          </Button>
        }
      />
      <div className="space-y-4 p-4">
        <p className="text-xs text-muted">
          Try other decisions. Nothing here changes the optimizer&apos;s plan;
          the answer is a separate simulation.
        </p>
        <div className="grid gap-2 md:grid-cols-2">
          {result.plan.map((d) => (
            <label
              key={d.relationship_id}
              className="flex items-center justify-between gap-2 text-xs"
            >
              <span className="truncate font-mono" title={d.relationship_id}>
                {d.relationship_id}
              </span>
              <select
                aria-label={`Placement of ${d.relationship_id}`}
                className="rounded border border-line bg-surface px-2 py-1"
                value={choices[d.relationship_id] ?? "optimizer"}
                onChange={(e) =>
                  setChoices({
                    ...choices,
                    [d.relationship_id]: e.target.value as Choice,
                  })
                }
              >
                <option value="optimizer">optimizer: {d.decision}</option>
                <option value="EMBED">EMBED</option>
                <option value="REFERENCE">REFERENCE</option>
              </select>
            </label>
          ))}
        </div>
        {options.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {options.map((o) => (
              <label key={o.sql} className="flex items-center gap-1.5 text-xs">
                <input
                  type="checkbox"
                  checked={alters.includes(o.sql)}
                  onChange={() =>
                    setAlters(
                      alters.includes(o.sql)
                        ? alters.filter((s) => s !== o.sql)
                        : [...alters, o.sql],
                    )
                  }
                />
                {o.label}
              </label>
            ))}
          </div>
        )}
        {queryIds.length > 0 && (
          <div className="flex flex-wrap gap-3 text-xs">
            {queryIds.map((q) => (
              <label key={q} className="flex items-center gap-1.5">
                frequency of {q}
                <input
                  type="number"
                  min={1}
                  className="w-16 rounded border border-line bg-surface px-1 py-0.5"
                  value={freq[q] ?? 1}
                  onChange={(e) =>
                    setFreq({
                      ...freq,
                      [q]: Math.max(1, Number(e.target.value) || 1),
                    })
                  }
                />
              </label>
            ))}
          </div>
        )}
        {sim.error && (
          <ErrorState message={String(sim.error)} title="Simulation failed" />
        )}
        {sim.isPending && <p className="text-xs text-muted">Simulating</p>}
        {sim.data && dirty && <Result sim={sim.data} />}
      </div>
    </Card>
  );
}
