import { ArrowRight, CheckCircle2, ChevronDown, ChevronRight, Database, Leaf, Wrench } from "lucide-react";
import { useMemo, useState } from "react";

import type { CompileResponse, Verdict } from "../api/client";
import { STATUS_WORDS, plainRule } from "../lib/plainEnglish";
import { Card, CardHeader, CodeBlock, VerdictBadge, cx } from "./ui";

const TONE = {
  SAFE: "border-safe/40 bg-safe-soft text-safe",
  CHANGED: "border-changed/40 bg-changed-soft text-changed",
  BROKEN: "border-broken/40 bg-broken-soft text-broken",
} as const;

function Chevron({ open }: { open: boolean }) {
  const Icon = open ? ChevronDown : ChevronRight;
  return <Icon size={16} className="mt-0.5 shrink-0" aria-hidden />;
}

/** Block diagram: where the data comes from, what SchemaShift checks, and what arrives in MongoDB. */
function FlowDiagram({ counts }: { counts: Record<string, number> }) {
  const box = "rounded-lg border px-4 py-3 text-center";
  const arrow = "mx-auto shrink-0 rotate-90 text-muted 2xl:rotate-0";
  return (
    <div
      role="img"
      aria-label={`Diagram: your SQL database goes through SchemaShift. ${counts.SAFE ?? 0} items move over cleanly, ${counts.CHANGED ?? 0} behave differently, ${counts.BROKEN ?? 0} will break without a fix.`}
      className="flex flex-col items-stretch gap-3 2xl:flex-row 2xl:items-center"
    >
      <div className={cx(box, "border-line bg-surface 2xl:w-44")}>
        <Database size={20} className="mx-auto mb-1 text-muted" aria-hidden />
        <div className="text-sm font-semibold">Your SQL database</div>
        <div className="text-xs text-muted">Tables, rules and queries</div>
      </div>
      <ArrowRight size={18} className={arrow} aria-hidden />
      <div className={cx(box, "border-accent/40 bg-accent-soft text-accent 2xl:w-44")}>
        <Wrench size={20} className="mx-auto mb-1" aria-hidden />
        <div className="text-sm font-semibold">SchemaShift</div>
        <div className="text-xs opacity-80">Checks every rule</div>
      </div>
      <ArrowRight size={18} className={arrow} aria-hidden />
      <div className="grid flex-1 gap-2">
        {(["SAFE", "CHANGED", "BROKEN"] as const).map((s) => (
          <div key={s} className={cx("flex items-center gap-3 rounded-lg border px-3 py-2", TONE[s])}>
            <span className="w-10 text-xl font-semibold tabular">{counts[s] ?? 0}</span>
            <span className="text-sm font-medium">{STATUS_WORDS[s]}</span>
          </div>
        ))}
      </div>
      <ArrowRight size={18} className={arrow} aria-hidden />
      <div className={cx(box, "border-safe/40 bg-safe-soft text-safe 2xl:w-44")}>
        <Leaf size={20} className="mx-auto mb-1" aria-hidden />
        <div className="text-sm font-semibold">MongoDB</div>
        <div className="text-xs opacity-80">Ready after the fixes</div>
      </div>
    </div>
  );
}

interface Group {
  ruleId: string;
  status: "CHANGED" | "BROKEN";
  items: Verdict[];
}

function ProblemList({ verdicts }: { verdicts: Verdict[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const groups = useMemo(() => {
    const map = new Map<string, Group>();
    for (const v of verdicts) {
      if (v.status === "SAFE") continue;
      const key = `${v.rule_id}:${v.status}`;
      const g = map.get(key) ?? { ruleId: v.rule_id, status: v.status, items: [] };
      g.items.push(v);
      map.set(key, g);
    }
    return [...map.values()].sort((a, b) =>
      a.status === b.status ? b.items.length - a.items.length : a.status === "BROKEN" ? -1 : 1,
    );
  }, [verdicts]);

  if (groups.length === 0)
    return (
      <p className="flex items-center gap-2 px-4 py-4 text-sm text-safe">
        <CheckCircle2 size={16} aria-hidden /> Nothing needs fixing. Everything moves over cleanly.
      </p>
    );

  return (
    <ul className="divide-y divide-line">
      {groups.map((g) => {
        const id = `${g.ruleId}:${g.status}`;
        const p = plainRule(g.ruleId, g.items[0].reason, g.items[0].mitigation);
        const isOpen = open === id;
        return (
          <li key={id}>
            <button
              type="button"
              aria-expanded={isOpen}
              onClick={() => setOpen(isOpen ? null : id)}
              className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-raised"
            >
              <Chevron open={isOpen} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold">{p.title}</span>
                <span className="block text-sm text-muted">{p.what}</span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <span className="text-xs text-muted tabular">{g.items.length}x</span>
                <VerdictBadge status={g.status} />
              </span>
            </button>
            {isOpen && (
              <div className="space-y-3 bg-raised/50 px-4 pb-4 pl-11 pt-1 text-sm">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted">How to fix</div>
                  <p>{p.fix}</p>
                </div>
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted">Where it happens</div>
                  <ul className="list-disc pl-5 text-muted">
                    {g.items.slice(0, 6).map((v) => (
                      <li key={v.node_id}>{v.node_id.replace(/^[a-z_]+:/, "")}</li>
                    ))}
                    {g.items.length > 6 && <li>and {g.items.length - 6} more</li>}
                  </ul>
                </div>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Each translated query, the issues that touch it, and the MongoDB version that already applies the fix. */
function QueryList({ result }: { result: CompileResponse }) {
  const [open, setOpen] = useState<string | null>(null);
  const issuesFor = (qid: string) =>
    result.verdicts.filter((v) => v.status !== "SAFE" && new RegExp(`(^|[:#])${qid}($|[#:])`).test(v.node_id));
  if (result.queries.length === 0)
    return <p className="px-4 py-3 text-sm text-muted">Add queries in the Queries tab to see them translated here.</p>;
  return (
    <ul className="divide-y divide-line">
      {result.queries.map((q) => {
        const issues = issuesFor(q.query_id);
        const isOpen = open === q.query_id;
        const mongo = (q.mongosh ?? "")
          .split("\n")
          .filter((l) => !l.startsWith("// Generated") && !l.startsWith("// Run:"))
          .join("\n")
          .trim();
        return (
          <li key={q.query_id}>
            <button
              type="button"
              aria-expanded={isOpen}
              onClick={() => setOpen(isOpen ? null : q.query_id)}
              className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-raised"
            >
              <Chevron open={isOpen} />
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-semibold uppercase tracking-wide text-muted">{q.query_id}</span>
                <span className="block truncate font-mono text-xs">{q.sql}</span>
              </span>
              {q.error ? (
                <VerdictBadge status="BROKEN" />
              ) : issues.length > 0 ? (
                <span className="shrink-0 text-xs text-muted">{issues.length} to review</span>
              ) : (
                <VerdictBadge status="SAFE" />
              )}
            </button>
            {isOpen && (
              <div className="space-y-3 bg-raised/50 px-4 pb-4 pl-11 pt-1 text-sm">
                {q.error && <p className="text-broken">This query could not be translated: {q.error}</p>}
                {issues.map((v) => {
                  const p = plainRule(v.rule_id, v.reason, v.mitigation);
                  return (
                    <div key={v.node_id} className="rounded border border-line bg-bg p-3">
                      <div className="flex items-center gap-2">
                        <VerdictBadge status={v.status} />
                        <span className="font-semibold">{p.title}</span>
                      </div>
                      <p className="mt-1 text-muted">{v.reason}</p>
                      <p className="mt-1">
                        <span className="font-semibold">Fix: </span>
                        {v.mitigation ?? p.fix}
                      </p>
                    </div>
                  );
                })}
                {!q.error && (
                  <div>
                    <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                      MongoDB version (fix already applied)
                    </div>
                    <CodeBlock code={mongo} maxHeight="18rem" />
                  </div>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function OverviewTab({ result }: { result: CompileResponse }) {
  const headline =
    result.overall_verdict === "BROKEN"
      ? "Some parts will break without a fix."
      : result.overall_verdict === "CHANGED"
        ? "Everything works, but a few things behave differently."
        : "Everything moves over cleanly.";
  return (
    <div className="space-y-4 p-4">
      <div className={cx("rounded-lg border px-4 py-3", TONE[result.overall_verdict])}>
        <div className="text-xs font-medium uppercase tracking-wide opacity-80">Result</div>
        <div className="text-xl font-semibold" data-testid="overall-verdict">
          {result.overall_verdict}
        </div>
        <div className="text-sm">{headline}</div>
      </div>

      <Card>
        <CardHeader title="How your data moves" />
        <div className="p-4">
          <FlowDiagram counts={result.counts} />
        </div>
      </Card>

      <Card>
        <CardHeader title="What needs attention and how to fix it" />
        <ProblemList verdicts={result.verdicts} />
      </Card>

      <Card>
        <CardHeader title="Your queries, translated" />
        <QueryList result={result} />
      </Card>
    </div>
  );
}
