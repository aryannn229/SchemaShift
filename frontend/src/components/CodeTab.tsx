import { Download, FileCode2 } from "lucide-react";
import JSZip from "jszip";
import { useState } from "react";

import type { CompileResponse } from "../api/client";
import { Button, CodeBlock, EmptyState, cx } from "./ui";

const GROUPS = [
  ["validators", "Validators"],
  ["indexes", "Indexes"],
  ["queries", "Queries"],
  ["migration", "Migration"],
  ["helpers", "Enforcement Helpers"],
] as const;

export function CodeTab({ result }: { result: CompileResponse }) {
  const [purpose, setPurpose] = useState<string>("validators");
  const [path, setPath] = useState<string | null>(null);
  if (result.generated.length === 0) return <EmptyState title="No code generated" />;

  const files = result.generated.filter((f) => f.purpose === purpose);
  const current = files.find((f) => f.path === path) ?? files[0];

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
    <div className="space-y-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted tabular">{result.generated.length} generated files</span>
        <Button size="sm" onClick={() => void download()} icon={<Download size={13} aria-hidden />}>
          Download all
        </Button>
      </div>
      <div className="grid gap-3 md:grid-cols-[14rem_1fr]">
        <nav aria-label="Generated files" className="space-y-3 text-sm">
          {GROUPS.map(([id, label]) => {
            const group = result.generated.filter((f) => f.purpose === id);
            return (
              <div key={id}>
                <button
                  type="button"
                  aria-pressed={purpose === id}
                  onClick={() => {
                    setPurpose(id);
                    setPath(null);
                  }}
                  className={cx(
                    "w-full rounded px-2 py-1 text-left font-medium",
                    purpose === id ? "bg-raised text-fg" : "text-muted hover:text-fg",
                  )}
                >
                  {label} <span className="tabular text-xs text-muted">{group.length}</span>
                </button>
                {purpose === id && (
                  <ul className="mt-1 space-y-0.5 border-l border-line pl-2">
                    {group.map((f) => (
                      <li key={f.path}>
                        <button
                          type="button"
                          onClick={() => setPath(f.path)}
                          aria-current={current?.path === f.path}
                          className={cx(
                            "flex w-full items-center gap-1.5 truncate rounded px-2 py-0.5 text-left font-mono text-xs",
                            current?.path === f.path ? "bg-accent-soft text-accent" : "text-muted hover:text-fg",
                          )}
                        >
                          <FileCode2 size={12} aria-hidden className="shrink-0" />
                          <span className="truncate">{f.path.split("/").pop()}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </nav>
        <div className="min-w-0">
          {current ? (
            <CodeBlock code={current.content} title={current.path} maxHeight="32rem" />
          ) : (
            <EmptyState title="No files for this section" />
          )}
        </div>
      </div>
    </div>
  );
}
