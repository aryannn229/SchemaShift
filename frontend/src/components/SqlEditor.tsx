import Editor, { type OnMount } from "@monaco-editor/react";
import { useEffect, useRef } from "react";

export interface Highlight {
  lineStart: number;
  lineEnd: number;
}

export function SqlEditor({
  value,
  onChange,
  highlight,
  dark,
}: {
  value: string;
  onChange: (v: string) => void;
  highlight?: Highlight | null;
  dark: boolean;
}) {
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
  const decorations = useRef<string[]>([]);

  useEffect(() => {
    const ed = editorRef.current;
    if (!ed) return;
    decorations.current = ed.deltaDecorations(
      decorations.current,
      highlight
        ? [
            {
              range: {
                startLineNumber: highlight.lineStart,
                startColumn: 1,
                endLineNumber: highlight.lineEnd,
                endColumn: 1000,
              },
              options: { isWholeLine: true, className: "ss-highlight" },
            },
          ]
        : [],
    );
    if (highlight) ed.revealLineInCenter(highlight.lineStart);
  }, [highlight]);

  return (
    <Editor
      height="100%"
      language="sql"
      theme={dark ? "vs-dark" : "light"}
      value={value}
      onChange={(v) => onChange(v ?? "")}
      onMount={(ed) => {
        editorRef.current = ed;
      }}
      options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false }}
    />
  );
}
