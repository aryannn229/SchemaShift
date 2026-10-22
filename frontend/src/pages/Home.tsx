import { PanelLeftClose, PanelLeftOpen, Play, ShieldCheck, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Group, Panel, Separator, type PanelImperativeHandle } from "react-resizable-panels";

import type { CompileRequest, Verdict } from "../api/client";
import { fetchSample, useCompile, useRun, useSamples, useStartRun } from "../api/hooks";
import { ResultTabs } from "../components/ResultTabs";
import { SqlEditor, type Highlight } from "../components/SqlEditor";
import { Button, Tabs, cx } from "../components/ui";
import { useMediaQuery } from "../lib/useMediaQuery";

type EditorTab = "schema" | "queries" | "seed";

const EDITOR_TABS = [
  { id: "schema", label: "Schema" },
  { id: "queries", label: "Queries" },
  { id: "seed", label: "Seed (optional)" },
] as const;

export default function Home() {
  const navigate = useNavigate();
  const wide = useMediaQuery("(min-width: 1024px)");
  const [files, setFiles] = useState({ schema: "", queries: "", seed: "" });
  const [options, setOptions] = useState<CompileRequest["options"]>({});
  const [optionsText, setOptionsText] = useState("{}");
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [tab, setTab] = useState<EditorTab>("schema");
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [runId, setRunId] = useState<string | undefined>();
  const [collapsed, setCollapsed] = useState(false);
  const editorPanel = useRef<PanelImperativeHandle | null>(null);
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
      relationship_overrides: {
        ...(options?.relationship_overrides ?? {}),
        [relationshipId]: decision,
      },
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

  const toggleCollapse = () => {
    const p = editorPanel.current;
    if (!p) return;
    if (p.isCollapsed()) p.expand();
    else p.collapse();
  };

  const verifyRun = run.data;
  const result = verifyRun?.result ?? compile.data;
  const empty = !files.schema.trim();

  const editor = (
    <section aria-label="Editor" className="flex h-full min-h-[28rem] flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-2 py-1">
        <Tabs
          tabs={EDITOR_TABS}
          value={tab}
          label="Input files"
          variant="pill"
          onChange={(t) => {
            setTab(t);
            setHighlight(null);
          }}
        />
        <div className="ml-auto flex items-center gap-2">
          <select
            aria-label="Load sample"
            className="h-8 rounded border border-line bg-surface px-2 text-sm"
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
          <Button size="sm" onClick={() => void runDemo()} icon={<Sparkles size={13} aria-hidden />}>
            Demo
          </Button>
          <Button size="sm" aria-expanded={optionsOpen} onClick={() => setOptionsOpen(!optionsOpen)}>
            Options
          </Button>
        </div>
      </div>
      {optionsOpen && (
        <textarea
          aria-label="Options JSON"
          spellCheck={false}
          className="h-32 border-b border-line bg-bg p-2 font-mono text-xs"
          value={optionsText}
          onChange={(e) => applyOptions(e.target.value)}
        />
      )}
      <div className="min-h-[18rem] flex-1">
        <SqlEditor
          key={tab}
          label={`${tab} editor`}
          value={files[tab]}
          onChange={(v) => setFiles((f) => ({ ...f, [tab]: v }))}
          highlight={highlight}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-line px-2 py-2">
        <Button
          variant="primary"
          onClick={doCompile}
          disabled={empty || compile.isPending}
          icon={<Play size={13} aria-hidden />}
        >
          Compile
        </Button>
        <Button
          onClick={doVerify}
          disabled={empty || startRun.isPending}
          icon={<ShieldCheck size={13} aria-hidden />}
        >
          Run verification
        </Button>
        {verifyRun?.status === "done" && (
          <Button variant="ghost" onClick={() => navigate(`/runs/${verifyRun.id}`)}>
            Open shareable run
          </Button>
        )}
        <span className="ml-auto text-xs text-muted">Ctrl/Cmd+Enter compiles</span>
      </div>
    </section>
  );

  const results = (
    <section aria-label="Results" className="h-full">
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
  );

  if (!wide)
    return (
      <div className="space-y-4 p-4">
        <div className="rounded-lg border border-line">{editor}</div>
        <div className="rounded-lg border border-line">{results}</div>
      </div>
    );

  return (
    <div className="mx-auto h-[calc(100vh-7.5rem)] max-w-screen-2xl p-4">
      <Group orientation="horizontal" className="h-full">
        <Panel
          id="editor"
          defaultSize="45"
          minSize="25"
          collapsible
          collapsedSize="0"
          panelRef={editorPanel}
          onResize={(s) => setCollapsed(s.asPercentage < 1)}
          className="overflow-hidden rounded-lg border border-line"
        >
          {editor}
        </Panel>
        <Separator
          aria-label="Resize editor and results"
          className={cx("group relative mx-1 w-2 shrink-0 cursor-col-resize rounded")}
        >
          <div className="mx-auto h-full w-px bg-line group-hover:bg-accent group-data-[separator=active]:bg-accent" />
        </Separator>
        <Panel id="results" minSize="30" className="overflow-hidden rounded-lg border border-line">
          <div className="relative h-full">
            <Button
              size="sm"
              variant="ghost"
              aria-label={collapsed ? "Expand editor" : "Collapse editor"}
              onClick={toggleCollapse}
              className="absolute right-2 top-1.5 z-10"
              icon={
                collapsed ? (
                  <PanelLeftOpen size={14} aria-hidden />
                ) : (
                  <PanelLeftClose size={14} aria-hidden />
                )
              }
            />
            {results}
          </div>
        </Panel>
      </Group>
    </div>
  );
}
