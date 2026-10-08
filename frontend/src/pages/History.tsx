import { useState } from "react";
import { Link } from "react-router-dom";

import { useRuns } from "../api/hooks";
import { Badge } from "../components/Badge";
import { Empty, ErrorBox, Loading } from "../components/States";

export default function History() {
  const [page, setPage] = useState(1);
  const runs = useRuns(page);
  if (runs.isPending) return <Loading label="Loading history…" />;
  if (runs.isError) return <ErrorBox message={(runs.error as Error).message} />;
  const { items, total, page_size } = runs.data;
  if (items.length === 0) return <Empty>No runs yet. Use Compile &amp; Verify on the home page.</Empty>;
  return (
    <div className="p-4">
      <h1 className="mb-3 text-xl font-bold">History</h1>
      <ul className="divide-y dark:divide-slate-700">
        {items.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            {r.overall_verdict ? <Badge value={r.overall_verdict} /> : <span>{r.status}</span>}
            <Link className="font-mono text-indigo-600 underline" to={`/runs/${r.id}`}>
              {r.id.slice(0, 8)}
            </Link>
            <span className="truncate text-slate-500">{r.schema_preview}</span>
            <span className="ml-auto text-xs text-slate-500">
              {new Date(r.created_at).toLocaleString()}
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex gap-2">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
          Previous
        </button>
        <button disabled={page * page_size >= total} onClick={() => setPage(page + 1)}>
          Next
        </button>
      </div>
    </div>
  );
}
