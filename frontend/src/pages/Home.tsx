import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";

import { fetchSample, useCompile, useRun, useSamples, useStartRun } from "../api/hooks";
import type { CompileRequest, Verdict } from "../api/client";
import { ResultTabs } from "../components/ResultTabs";
import { SqlEditor, type Highlight } from "../components/SqlEditor";

type EditorTab = "schema" | "queries" | "seed";

export default function Home() {
  const { dark } = useOutletContext<{ dark: boolean }>();
  const navigate = useNavigate();
  const [files, setFiles] = useState({ schema: "", queries: "", seed: "" });
  const [options, setOptions] = useState<CompileRequest["options"]>({});
  const [optionsText, setOptionsText] = useState("{}");
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [tab, setTab] = useState<EditorTab>("schema");
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [runId, setRunId] = useState<string | undefined>();
  const samples = useSamples();
  const compile = useCompile();
  const startRun = useStartRun();
  const run = useRun(runId);

  const request = useMemo(
    () => ({
      schema_sql: files.schema,
      queries_sql: files.queries,
      seed_sql: files.seed,
      options,
    }),
    [files, options],
  );

  const doCompile = useCallback(() => {
    setRunId(undefined);
    compile.mutate(request);
  }, [compile, request]);

  const doVerify = () => {
    startRun.mutate(
      { ...request, verify: true, seed: 1 },
      { onSuccess: (r) => setRunId(r.run_id) },
    );
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        doCompile();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doCompile]);

  const loadSample = async (name: string) => {
    if (!name) return null;
    const s = await fetchSample(name);
    const opts = (s.options ?? {}) as CompileRequest["options"];
    setFiles({ schema: s.schema_sql, queries: s.queries_sql, seed: "" });
    setOptions(opts);
    setOptionsText(JSON.stringify(opts, null, 2));
    setRunId(undefined);
    compile.reset();
    return { schema_sql: s.schema_sql, queries_sql: s.queries_sql, seed_sql: "", options: opts };
  };

  /** Class demo: load the banking sample, compile it, and start a verified run when available. */
  const runDemo = async () => {
    const req = await loadSample("banking");
    if (!req) return;
    compile.mutate(req);
    startRun.mutate({ ...req, verify: true, seed: 1 }, { onSuccess: (r) => setRunId(r.run_id) });
  };

  const selectVerdict = (v: Verdict) => {
    const span = v.source_span;
    if (!span) return setHighlight(null);
    const schemaLines = files.schema.split("\n").length;
    if (span.line_start <= schemaLines) {
      setTab("schema");
      setHighlight({ lineStart: span.line_start, lineEnd: span.line_end });
    } else {
      setTab("queries");
      const offset = compile.data?.queries_line_offset ?? schemaLines;
      setHighlight({
        lineStart: Math.max(1, span.line_start - offset),
        lineEnd: Math.max(1, span.line_end - offset),
      });
    }
  };

  const override = (relationshipId: string, decision: "EMBED" | "REFERENCE") => {
    const next = {
      ...options,
      relationship_overrides: { ...(options?.relationship_overrides ?? {}), [relationshipId]: decision },
    };
    setOptions(next);
    setOptionsText(JSON.stringify(next, null, 2));
    compile.mutate({ ...request, options: next });
  };

  const applyOptions = (text: string) => {
    setOptionsText(text);
    try {
      setOptions(JSON.parse(text) as CompileRequest["options"]);
    } catch {
      /* keep typing */
    }
  };

  const verifyRun = run.data;
  const result = verifyRun?.result ?? compile.data;

  return (
    <div className="grid gap-4 p-4 lg:grid-cols-2">
      <section className="flex min-h-[32rem] flex-col rounded border dark:border-slate-700">
        <div className="flex flex-wrap items-center gap-2 border-b p-2 dark:border-slate-700">
          {(["schema", "queries", "seed"] as const).map((t) => (
            <button
              key={t}
              onClick={() => {
                setTab(t);
                setHighlight(null);
              }}
              aria-pressed={tab === t}
              className={`rounded px-2 py-1 text-sm ${tab === t ? "bg-slate-200 font-semibold dark:bg-slate-700" : ""}`}
            >
              {t === "seed" ? "Seed (optional)" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
          <select
            aria-label="Load sample"
            className="ml-auto rounded border px-1 text-sm dark:border-slate-600 dark:bg-slate-800"
            value=""
            onChange={(e) => void loadSample(e.target.value)}
          >
            <option value="">Load sample…</option>
            {samples.data?.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
          <button
            onClick={() => void runDemo()}
            className="rounded bg-emerald-600 px-2 py-1 text-sm font-semibold text-white"
          >
            Demo
          </button>
          <button
            onClick={() => setOptionsOpen(!optionsOpen)}
            className="rounded border px-2 py-1 text-sm dark:border-slate-600"
          >
            Options
          </button>
        </div>
        {optionsOpen && (
          <textarea
            aria-label="Options JSON"
            className="h-32 border-b p-2 font-mono text-xs dark:border-slate-700 dark:bg-slate-900"
            value={optionsText}
            onChange={(e) => applyOptions(e.target.value)}
          />
        )}
        <div className="min-h-[20rem] flex-1">
          <SqlEditor
            key={tab}
            value={files[tab]}
            onChange={(v) => setFiles((f) => ({ ...f, [tab]: v }))}
            highlight={highlight}
            dark={dark}
          />
        </div>
        <div className="flex gap-2 border-t p-2 dark:border-slate-700">
          <button
            onClick={doCompile}
            disabled={!files.schema.trim() || compile.isPending}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
          >
            Compile
          </button>
          <button
            onClick={doVerify}
            disabled={!files.schema.trim() || startRun.isPending}
            className="rounded border px-3 py-1.5 text-sm disabled:opacity-50 dark:border-slate-600"
          >
            Compile &amp; Verify
          </button>
          {verifyRun?.status === "done" && (
            <button
              onClick={() => navigate(`/runs/${verifyRun.id}`)}
              className="ml-auto text-sm text-indigo-600 underline"
            >
              Open shareable run
            </button>
          )}
          <span className="ml-auto self-center text-xs text-slate-500">Ctrl/Cmd+Enter compiles</span>
        </div>
      </section>
      <section className="rounded border dark:border-slate-700">
        <ResultTabs
          result={result}
          loading={compile.isPending}
          error={(compile.error as Error | null)?.message}
          verifyError={(startRun.error as Error | null)?.message}
          run={verifyRun}
          onSelectVerdict={selectVerdict}
          onOverride={override}
        />
      </section>
    </div>
  );
}
