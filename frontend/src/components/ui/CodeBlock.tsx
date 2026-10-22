import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "./Button";

export function CodeBlock({
  code,
  title,
  maxHeight = "28rem",
  testId,
}: {
  code: string;
  title?: string;
  maxHeight?: string;
  testId?: string;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <span className="font-mono text-xs text-muted">{title}</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void copy()}
          icon={copied ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
          aria-label={title ? `Copy ${title}` : "Copy code"}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre
        tabIndex={0}
        data-testid={testId}
        className="overflow-auto p-3 font-mono text-xs leading-5"
        style={{ maxHeight }}
      >
        {code}
      </pre>
    </div>
  );
}
