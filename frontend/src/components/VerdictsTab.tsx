import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useMemo, useState } from "react";

import type { Counterexample, CounterexampleGap, CompileResponse, RunDetail, Verdict } from "../api/client";
import { useReplayCounterexample } from "../api/hooks";
import { CounterexampleView } from "./CounterexampleView";
import { EvidencePanel, effectiveLevel } from "./EvidencePanel";
import { EmptyState, EvidenceBadge, Table, Td, Th, THead, VerdictBadge } from "./ui";

type SortKey = "status" | "node_id" | "rule_id" | "evidence";
const ORDER: Record<string, number> = { BROKEN: 0, CHANGED: 1, SAFE: 2 };
const EVIDENCE_ORDER: Record<string, number> = { PROVEN: 0, CONDITIONAL: 1, TESTED: 2, UNVERIFIED: 3 };
const LABELS: Record<SortKey, string> = {
  status: "Status",
  node_id: "IR node",
  rule_id: "Rule",
  evidence: "Evidence",
};

function Details({ verdict, run }: { verdict: Verdict; run?: RunDetail }) {
  const upgrade = run?.verification?.evidence_upgrades?.find((u) => u.verdict_id === verdict.node_id);
  const stored = run?.verification?.counterexamples?.find((c) => c.verdict_id === verdict.node_id);
  const storedGap = run?.verification?.counterexample_gaps?.find((g) => g.verdict_id === verdict.node_id);
  const replay = useReplayCounterexample(run?.id);
  const replayed = replay.data;
  const example: Counterexample | undefined = replayed?.counterexample ?? (replayed?.gap ? undefined : stored);
  const gap: CounterexampleGap | undefined = replayed?.gap ?? (replayed?.counterexample ? undefined : storedGap);
  const verified = !!run?.verification;
  return (
    <div className="space-y-4">
      <EvidencePanel verdict={verdict} upgrade={upgrade} />
      <div className="text-xs">
        <b>Why?</b>
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {(verdict.conditions_evaluated ?? []).map((c, i) => (
            <li key={i}>
              {c.condition}: <b>{String(c.result)}</b>
              {c.detail ? `: ${c.detail}` : ""}
            </li>
          ))}
        </ul>
      </div>
      {verdict.status !== "SAFE" &&
        (verified ? (
          <CounterexampleView
            example={example}
            gap={gap}
            onReplay={run?.status === "done" ? () => replay.mutate(verdict.node_id) : undefined}
            replaying={replay.isPending}
            replayError={(replay.error as Error | null)?.message}
          />
        ) : (
          <p className="text-xs text-muted">Run verification to generate a minimal counterexample.</p>
        ))}
    </div>
  );
}

export function VerdictsTab({
  result,
  run,
  onSelect,
}: {
  result: CompileResponse;
  run?: RunDetail;
  onSelect?: (v: Verdict) => void;
}) {
  const [filter, setFilter] = useState<string>("ALL");
  const [evidence, setEvidence] = useState<string>("ALL");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("status");
  const [desc, setDesc] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const upgrades = run?.verification?.evidence_upgrades;

  const levelOf = (v: Verdict) => effectiveLevel(v, upgrades?.find((u) => u.verdict_id === v.node_id));

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = result.verdicts.filter(
      (v) =>
        (filter === "ALL" || v.status === filter) &&
        (evidence === "ALL" ||
          effectiveLevel(v, upgrades?.find((u) => u.verdict_id === v.node_id)) === evidence) &&
        (!q || `${v.node_id} ${v.rule_id} ${v.reason}`.toLowerCase().includes(q)),
    );
    const key = (v: Verdict) =>
      effectiveLevel(v, upgrades?.find((u) => u.verdict_id === v.node_id));
    const sorted = [...filtered].sort((a, b) =>
      sort === "status"
        ? ORDER[a.status] - ORDER[b.status]
        : sort === "evidence"
          ? EVIDENCE_ORDER[key(a)] - EVIDENCE_ORDER[key(b)]
          : String(a[sort]).localeCompare(String(b[sort])),
    );
    return desc ? sorted.reverse() : sorted;
  }, [result.verdicts, filter, evidence, query, sort, desc, upgrades]);

  if (result.verdicts.length === 0) return <EmptyState title="No verdicts for this input" />;

  const sortBy = (k: SortKey) => {
    if (sort === k) setDesc(!desc);
    else {
      setSort(k);
      setDesc(false);
    }
  };
  const dir = (k: SortKey) => (sort === k ? (desc ? "desc" : "asc") : null);

  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label htmlFor="verdict-filter" className="text-muted">
          Filter
        </label>
        <select
          id="verdict-filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="h-8 rounded border border-line bg-surface px-2"
        >
          {["ALL", "BROKEN", "CHANGED", "SAFE"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <label htmlFor="evidence-filter" className="text-muted">
          Evidence
        </label>
        <select
          id="evidence-filter"
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          className="h-8 rounded border border-line bg-surface px-2"
        >
          {["ALL", "PROVEN", "CONDITIONAL", "TESTED", "UNVERIFIED"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <input
          aria-label="Search verdicts"
          placeholder="Search node, rule or reason"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="h-8 min-w-0 flex-1 rounded border border-line bg-surface px-2 sm:max-w-xs"
        />
        <span className="tabular text-xs text-muted">
          {rows.length} of {result.verdicts.length}
        </span>
      </div>

      <Table className="max-h-[calc(100vh-18rem)]">
        <THead>
          <tr>
            <Th className="w-8">
              <span className="sr-only">Expand</span>
            </Th>
            {(["status", "evidence", "node_id", "rule_id"] as const).map((k) => (
              <Th key={k} sortDir={dir(k)} onSort={() => sortBy(k)}>
                {LABELS[k]}
              </Th>
            ))}
            <Th>Reason</Th>
            <Th>Mitigation</Th>
          </tr>
        </THead>
        <tbody>
          {rows.map((v) => {
            const expanded = open === v.node_id;
            return (
              <Fragment key={v.node_id}>
                <tr
                  className="cursor-pointer hover:bg-raised"
                  onClick={() => {
                    onSelect?.(v);
                    setOpen(expanded ? null : v.node_id);
                  }}
                >
                  <Td>
                    <button
                      type="button"
                      aria-expanded={expanded}
                      aria-label={`${expanded ? "Collapse" : "Expand"} trace for ${v.node_id}`}
                      className="text-muted"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpen(expanded ? null : v.node_id);
                      }}
                    >
                      {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </button>
                  </Td>
                  <Td>
                    <VerdictBadge status={v.status} />
                  </Td>
                  <Td>
                    <EvidenceBadge level={levelOf(v)} />
                  </Td>
                  <Td className="font-mono text-xs">{v.node_id}</Td>
                  <Td className="text-xs">{v.rule_id}</Td>
                  <Td>{v.reason}</Td>
                  <Td className="text-muted">{v.mitigation ?? "–"}</Td>
                </tr>
                {expanded && (
                  <tr className="bg-raised/50">
                    <Td colSpan={7}>
                      <Details verdict={v} run={run} />
                    </Td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
