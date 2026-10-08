import { useParams } from "react-router-dom";

import { useRun } from "../api/hooks";
import { Badge } from "../components/Badge";
import { ResultTabs } from "../components/ResultTabs";
import { ErrorBox, Loading } from "../components/States";

export default function RunPage() {
  const { id } = useParams();
  const run = useRun(id);
  if (run.isPending) return <Loading label="Loading run…" />;
  if (run.isError) return <ErrorBox message={(run.error as Error).message} />;
  const r = run.data;
  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Run {r.id.slice(0, 8)}</h1>
        {r.overall_verdict && <Badge value={r.overall_verdict} />}
        <span className="text-sm text-slate-500">{new Date(r.created_at).toLocaleString()}</span>
        {r.status === "done" && (
          <a className="ml-auto text-sm text-indigo-600 underline" href={`/api/v1/runs/${r.id}/export`}>
            Download zip
          </a>
        )}
      </div>
      <div className="rounded border dark:border-slate-700">
        <ResultTabs result={r.result} run={r} error={r.status === "failed" ? r.error : null} />
      </div>
      <details className="rounded border p-2 dark:border-slate-700">
        <summary className="cursor-pointer text-sm">Input SQL (read-only)</summary>
        <pre className="overflow-auto text-xs">{r.schema_sql}</pre>
        <pre className="overflow-auto text-xs">{r.queries_sql}</pre>
      </details>
    </div>
  );
}
