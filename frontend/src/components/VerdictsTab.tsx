import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useMemo, useState } from "react";

import type { CompileResponse, Verdict } from "../api/client";
import { EmptyState, Table, Td, Th, THead, VerdictBadge } from "./ui";

type SortKey = "status" | "node_id" | "rule_id";
const ORDER: Record<string, number> = { BROKEN: 0, CHANGED: 1, SAFE: 2 };
const LABELS: Record<SortKey, string> = { status: "Status", node_id: "IR node", rule_id: "Rule" };

export function VerdictsTab({
  result,
  onSelect,
}: {
  result: CompileResponse;
  onSelect?: (v: Verdict) => void;
}) {
  const [filter, setFilter] = useState<string>("ALL");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("status");
  const [desc, setDesc] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = result.verdicts.filter(
      (v) =>
        (filter === "ALL" || v.status === filter) &&
        (!q || `${v.node_id} ${v.rule_id} ${v.reason}`.toLowerCase().includes(q)),
    );
    const sorted = [...filtered].sort((a, b) =>
      sort === "status"
        ? ORDER[a.status] - ORDER[b.status]
        : String(a[sort]).localeCompare(String(b[sort])),
    );
    return desc ? sorted.reverse() : sorted;
  }, [result.verdicts, filter, query, sort, desc]);

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
            {(["status", "node_id", "rule_id"] as const).map((k) => (
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
                  <Td className="font-mono text-xs">{v.node_id}</Td>
                  <Td className="text-xs">{v.rule_id}</Td>
                  <Td>{v.reason}</Td>
                  <Td className="text-muted">{v.mitigation ?? "–"}</Td>
                </tr>
                {expanded && (
                  <tr className="bg-raised/50">
                    <Td colSpan={6} className="text-xs">
                      <b>Why?</b>
                      <ul className="mt-1 list-disc space-y-0.5 pl-5">
                        {(v.conditions_evaluated ?? []).map((c, i) => (
                          <li key={i}>
                            {c.condition}: <b>{String(c.result)}</b>
                            {c.detail ? `: ${c.detail}` : ""}
                          </li>
                        ))}
                      </ul>
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
