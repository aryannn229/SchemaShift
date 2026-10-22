import { Download } from "lucide-react";
import { useEffect } from "react";
import { useParams } from "react-router-dom";

import { useRun } from "../api/hooks";
import { ResultTabs } from "../components/ResultTabs";
import { SqlEditor } from "../components/SqlEditor";
import { Badge, Card, CardHeader, ErrorState, SkeletonBlock } from "../components/ui";

function setMeta(property: string, content: string) {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[property="${property}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute("property", property);
    document.head.appendChild(el);
  }
  el.content = content;
}

export default function RunPage() {
  const { id } = useParams();
  const run = useRun(id);
  const r = run.data;

  // Shareable run pages get a descriptive title and Open Graph tags.
  useEffect(() => {
    if (!r) return;
    const title = `Run ${r.id.slice(0, 8)}${r.overall_verdict ? `: ${r.overall_verdict}` : ""} · SchemaShift`;
    document.title = title;
    setMeta("og:title", title);
    setMeta(
      "og:description",
      `SchemaShift migration report. Overall verdict ${r.overall_verdict ?? "pending"}.`,
    );
  }, [r]);

  if (run.isPending) return <SkeletonBlock rows={8} label="Loading run" />;
  if (run.isError) return <ErrorState message={(run.error as Error).message} title="Run not found" />;
  const d = run.data;
  return (
    <div className="mx-auto max-w-screen-2xl space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold">Run {d.id.slice(0, 8)}</h2>
        {d.overall_verdict && <Badge value={d.overall_verdict} />}
        <span className="text-sm text-muted">{new Date(d.created_at).toLocaleString()}</span>
        {d.status === "done" && (
          <a
            className="ml-auto inline-flex h-8 items-center gap-1.5 rounded border border-line px-3 text-sm hover:bg-raised"
            href={`/api/v1/runs/${d.id}/export`}
          >
            <Download size={13} aria-hidden /> Download zip
          </a>
        )}
      </div>
      <div className="rounded-lg border border-line">
        <ResultTabs result={d.result} run={d} error={d.status === "failed" ? d.error : null} />
      </div>
      <Card>
        <CardHeader title="Input SQL (read-only)" />
        <div className="grid gap-px md:grid-cols-2">
          <div className="h-64">
            <SqlEditor label="Schema SQL" value={d.schema_sql} readOnly />
          </div>
          <div className="h-64">
            <SqlEditor label="Queries SQL" value={d.queries_sql} readOnly />
          </div>
        </div>
      </Card>
    </div>
  );
}
