import type { CompileResponse } from "../api/client";
import { Empty } from "./States";

export function IRTab({ result }: { result: CompileResponse }) {
  if (!result.ir_text) return <Empty>No IR produced.</Empty>;
  return (
    <pre className="overflow-auto p-4 font-mono text-xs" data-testid="ir-text">
      {result.ir_text}
    </pre>
  );
}
