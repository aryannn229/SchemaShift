import { useState } from "react";
import { Link } from "react-router-dom";

import { useRuns } from "../api/hooks";
import { Badge, Button, EmptyState, ErrorState, SkeletonBlock, Table, Td, Th, THead } from "../components/ui";

export default function History() {
  const [page, setPage] = useState(1);
  const runs = useRuns(page);
  if (runs.isPending) return <SkeletonBlock rows={8} label="Loading history" />;
  if (runs.isError) return <ErrorState message={(runs.error as Error).message} />;
  const { items, total, page_size } = runs.data;
  if (items.length === 0)
    return (
      <EmptyState title="No runs yet">
        Use Run verification on the Compile page to create a stored, shareable run.
      </EmptyState>
    );
  return (
    <div className="mx-auto max-w-screen-xl space-y-3 p-4">
      <h2 className="text-xl font-semibold">History</h2>
      <Table>
        <THead>
          <tr>
            <Th>Verdict</Th>
            <Th>Run</Th>
            <Th>Schema</Th>
            <Th className="text-right">Created</Th>
          </tr>
        </THead>
        <tbody>
          {items.map((r) => (
            <tr key={r.id} className="hover:bg-raised">
              <Td>{r.overall_verdict ? <Badge value={r.overall_verdict} /> : <Badge value={r.status} />}</Td>
              <Td>
                <Link className="font-mono text-xs text-accent underline" to={`/runs/${r.id}`}>
                  {r.id.slice(0, 8)}
                </Link>
              </Td>
              <Td className="max-w-md truncate text-muted">{r.schema_preview}</Td>
              <Td className="tabular text-right text-xs text-muted">
                {new Date(r.created_at).toLocaleString()}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
          Previous
        </Button>
        <Button size="sm" disabled={page * page_size >= total} onClick={() => setPage(page + 1)}>
          Next
        </Button>
        <span className="tabular text-xs text-muted">
          Page {page} · {total} runs
        </span>
      </div>
    </div>
  );
}
