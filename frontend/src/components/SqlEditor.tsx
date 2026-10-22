import Editor, { type BeforeMount, type OnMount } from "@monaco-editor/react";
import { useEffect, useRef } from "react";

import { useTheme } from "../lib/ThemeContext";

export interface Highlight {
  lineStart: number;
  lineEnd: number;
}

/** Editor themes built from the design tokens; every token colour is AA on the background. */
const defineThemes: BeforeMount = (monaco) => {
  monaco.editor.defineTheme("schemashift-light", {
    base: "vs",
    inherit: true,
    rules: [
      { token: "string.sql", foreground: "a31515" },
      { token: "comment.quote.sql", foreground: "5f6b7a" },
      { token: "comment.sql", foreground: "5f6b7a" },
      { token: "keyword.sql", foreground: "0b3fa8" },
      { token: "number.sql", foreground: "116329" },
      { token: "predefined.sql", foreground: "7a3e00" },
      { token: "operator.sql", foreground: "3d4a5c" },
      { token: "delimiter.sql", foreground: "3d4a5c" },
      { token: "identifier.sql", foreground: "0f172a" },
    ],
    colors: { "editor.background": "#ffffff" },
  });
  monaco.editor.defineTheme("schemashift-dark", {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "string.sql", foreground: "ce9178" },
      { token: "comment.quote.sql", foreground: "8b9bb0" },
      { token: "comment.sql", foreground: "8b9bb0" },
      { token: "keyword.sql", foreground: "79b8ff" },
      { token: "number.sql", foreground: "b5cea8" },
      { token: "predefined.sql", foreground: "dcdcaa" },
      { token: "operator.sql", foreground: "c0cddb" },
      { token: "delimiter.sql", foreground: "c0cddb" },
      { token: "identifier.sql", foreground: "e2e8f0" },
    ],
    colors: { "editor.background": "#0b1220" },
  });
};

export function SqlEditor({
  value,
  onChange,
  highlight,
  readOnly,
  label,
}: {
  value: string;
  onChange?: (v: string) => void;
  highlight?: Highlight | null;
  readOnly?: boolean;
  label?: string;
}) {
  const { dark } = useTheme();
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
    <div role="group" aria-label={label ?? "SQL editor"} className="h-full">
      <Editor
        height="100%"
        language="sql"
        theme={dark ? "schemashift-dark" : "schemashift-light"}
        beforeMount={defineThemes}
        value={value}
        onChange={(v) => onChange?.(v ?? "")}
        onMount={(ed) => {
          editorRef.current = ed;
        }}
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily: '"JetBrains Mono Variable", "JetBrains Mono", ui-monospace, monospace',
          scrollBeyondLastLine: false,
          readOnly,
          padding: { top: 8 },
          renderLineHighlight: "none",
        }}
      />
    </div>
  );
}
