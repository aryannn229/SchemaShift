import { Fragment, useMemo, useState } from "react";

import type { CompileResponse, Verdict } from "../api/client";
import { Badge } from "./Badge";
import { Empty } from "./States";

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
  const [sort, setSort] = useState<SortKey>("status");
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => {
    const filtered = result.verdicts.filter((v) => filter === "ALL" || v.status === filter);
    return [...filtered].sort((a, b) =>
      sort === "status"
        ? ORDER[a.status] - ORDER[b.status]
        : String(a[sort]).localeCompare(String(b[sort])),
    );
  }, [result.verdicts, filter, sort]);

  if (result.verdicts.length === 0) return <Empty>No verdicts for this input.</Empty>;

  return (
    <div className="p-4">
      <div className="mb-2 flex items-center gap-2 text-sm">
        <label htmlFor="verdict-filter">Filter</label>
        <select
          id="verdict-filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="rounded border px-1 dark:border-slate-600 dark:bg-slate-800"
        >
          {["ALL", "BROKEN", "CHANGED", "SAFE"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b dark:border-slate-700">
              {(["status", "node_id", "rule_id"] as const).map((k) => (
                <th key={k} className="p-1">
                  <button onClick={() => setSort(k)} className="font-semibold">
                    {LABELS[k]}
                    {sort === k ? " ▾" : ""}
                  </button>
                </th>
              ))}
              <th className="p-1">Reason</th>
              <th className="p-1">Mitigation</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <Fragment key={v.node_id}>
                <tr
                  className="cursor-pointer border-b align-top hover:bg-slate-100 dark:border-slate-800 dark:hover:bg-slate-800"
                  onClick={() => {
                    onSelect?.(v);
                    setOpen(open === v.node_id ? null : v.node_id);
                  }}
                >
                  <td className="p-1">
                    <Badge value={v.status} />
                  </td>
                  <td className="p-1 font-mono text-xs">{v.node_id}</td>
                  <td className="p-1 text-xs">{v.rule_id}</td>
                  <td className="p-1">{v.reason}</td>
                  <td className="p-1 text-slate-600 dark:text-slate-400">{v.mitigation ?? "—"}</td>
                </tr>
                {open === v.node_id && (
                  <tr className="bg-slate-50 dark:bg-slate-800/50">
                    <td colSpan={5} className="p-2 text-xs">
                      <b>Why?</b>
                      <ul className="mt-1 list-disc pl-5">
                        {(v.conditions_evaluated ?? []).map((c, i) => (
                          <li key={i}>
                            {c.condition}: <b>{String(c.result)}</b>
                            {c.detail ? ` — ${c.detail}` : ""}
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
