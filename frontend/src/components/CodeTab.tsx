import JSZip from "jszip";
import { useState } from "react";

import type { CompileResponse } from "../api/client";
import { Empty } from "./States";

const SUBTABS = [
  ["validators", "Validators"],
  ["indexes", "Indexes"],
  ["queries", "Queries"],
  ["migration", "Migration"],
  ["helpers", "Enforcement Helpers"],
] as const;

export function CodeTab({ result }: { result: CompileResponse }) {
  const [purpose, setPurpose] = useState<string>("validators");
  const files = result.generated.filter((f) => f.purpose === purpose);
  if (result.generated.length === 0) return <Empty>No code generated.</Empty>;

  const download = async () => {
    const zip = new JSZip();
    for (const f of result.generated) zip.file(f.path, f.content);
    const blob = await zip.generateAsync({ type: "blob" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "schemashift-output.zip";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {SUBTABS.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setPurpose(id)}
            aria-pressed={purpose === id}
            className={`rounded px-2 py-1 text-sm ${purpose === id ? "bg-indigo-600 text-white" : "border dark:border-slate-600"}`}
          >
            {label}
          </button>
        ))}
        <button
          onClick={download}
          className="ml-auto rounded border px-2 py-1 text-sm dark:border-slate-600"
        >
          Download zip
        </button>
      </div>
      {files.length === 0 && <Empty>No files for this section.</Empty>}
      {files.map((f) => (
        <div key={f.path} className="mb-4">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span className="font-mono">{f.path}</span>
            <button onClick={() => void navigator.clipboard?.writeText(f.content)}>Copy</button>
          </div>
          <pre className="max-h-96 overflow-auto rounded bg-slate-100 p-3 font-mono text-xs dark:bg-slate-800">
            {f.content}
          </pre>
        </div>
      ))}
    </div>
  );
}
