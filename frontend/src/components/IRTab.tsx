import type { CompileResponse } from "../api/client";
import { CodeBlock, EmptyState } from "./ui";

export function IRTab({ result }: { result: CompileResponse }) {
  if (!result.ir_text) return <EmptyState title="No IR produced" />;
  return (
    <div className="p-4">
      <CodeBlock code={result.ir_text} title={`${result.ir_node_count} IR nodes`} testId="ir-text" maxHeight="none" />
    </div>
  );
}
