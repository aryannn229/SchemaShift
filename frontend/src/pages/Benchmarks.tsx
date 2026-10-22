import { Upload } from "lucide-react";
import { useState } from "react";

import { BenchmarkView, parseBench, type BenchResults } from "../components/BenchmarkView";
import { CodeBlock, EmptyState, ErrorState } from "../components/ui";

/** Renders an uploaded benchmark results file. Unknown JSON falls back to a raw view. */
export default function Benchmarks() {
  const [text, setText] = useState<string | null>(null);
  const [data, setData] = useState<BenchResults | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const raw = await file.text();
      const parsed: unknown = JSON.parse(raw);
      setData(parseBench(parsed));
      setText(JSON.stringify(parsed, null, 2));
      setError(null);
    } catch {
      setText(null);
      setData(null);
      setError(`${file.name} is not valid JSON.`);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <div>
        <h2 className="text-2xl font-semibold">Benchmarks</h2>
        <p className="mt-1 text-sm text-muted">
          Benchmarks run from the command line (<span className="font-mono">schemashift bench</span>),
          never on this server. Upload a results file to inspect it here.
        </p>
      </div>
      <label className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded border border-line bg-surface px-3 text-sm font-medium hover:bg-raised focus-within:ring-2 focus-within:ring-accent">
        <Upload size={13} aria-hidden />
        Upload results file
        <input
          type="file"
          accept="application/json,.json"
          className="sr-only"
          onChange={(e) => void onFile(e.target.files?.[0])}
        />
      </label>
      {error && <ErrorState message={error} title="Could not read file" />}
      {data ? (
        <BenchmarkView data={data} />
      ) : text ? (
        <CodeBlock code={text} title="results.json" maxHeight="36rem" />
      ) : (
        !error && (
          <EmptyState title="No results loaded">
            Run a benchmark, then upload the generated JSON file.
          </EmptyState>
        )
      )}
    </div>
  );
}
