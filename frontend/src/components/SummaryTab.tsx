import type { CompileResponse } from "../api/client";
import { Badge } from "./Badge";

const BANNER: Record<string, string> = {
  SAFE: "border-green-400 bg-green-50 dark:bg-green-950",
  CHANGED: "border-amber-400 bg-amber-50 dark:bg-amber-950",
  BROKEN: "border-red-400 bg-red-50 dark:bg-red-950",
};

export function SummaryTab({ result }: { result: CompileResponse }) {
  return (
    <div className="space-y-4 p-4">
      <div className={`rounded border-l-4 p-4 ${BANNER[result.overall_verdict]}`}>
        <div className="text-sm uppercase tracking-wide text-slate-500">Overall verdict</div>
        <div className="text-2xl font-bold" data-testid="overall-verdict">
          {result.overall_verdict}
        </div>
      </div>
      <div className="flex gap-3">
        {(["SAFE", "CHANGED", "BROKEN"] as const).map((s) => (
          <div key={s} className="flex-1 rounded border p-3 text-center dark:border-slate-700">
            <div className="text-2xl font-semibold">{result.counts[s] ?? 0}</div>
            <Badge value={s} />
          </div>
        ))}
      </div>
      <section>
        <h3 className="mb-1 font-semibold">Diagnostics</h3>
        {result.diagnostics.length === 0 ? (
          <p className="text-sm text-slate-500">No diagnostics.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {result.diagnostics.map((d, i) => (
              <li key={i}>
                <span className="font-mono text-xs">{d.severity}</span> <b>{d.code}</b> {d.message}
                {d.location ? ` (line ${d.location.line_start})` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
