import { useMemo, useState } from "react";
import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";

import type { CompileResponse, PlacementDecision } from "../api/client";
import { Badge } from "./Badge";
import { Empty } from "./States";

export function RelationshipsTab({
  result,
  onOverride,
}: {
  result: CompileResponse;
  onOverride?: (relationshipId: string, decision: "EMBED" | "REFERENCE") => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);

  const nodes: Node[] = useMemo(
    () =>
      result.graph_nodes.map((n, i) => ({
        id: n.id,
        position: { x: (i % 4) * 220, y: Math.floor(i / 4) * 140 },
        data: { label: `${n.id} (${n.kind})` },
      })),
    [result.graph_nodes],
  );
  const edges: Edge[] = useMemo(
    () =>
      result.graph_edges.map((e) => ({
        id: e.id,
        source: e.child,
        target: e.parent,
        label: e.decision,
        animated: e.decision === "EMBED",
        style: { stroke: e.decision === "EMBED" ? "#4f46e5" : "#64748b", strokeWidth: 2 },
      })),
    [result.graph_edges],
  );

  if (nodes.length === 0) return <Empty>No tables to show.</Empty>;
  const decision: PlacementDecision | undefined = result.plan.find(
    (d) => d.relationship_id === selected,
  );

  return (
    <div className="flex h-full flex-col">
      <div className="h-80 border-b dark:border-slate-700" data-testid="relationship-graph">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          onEdgeClick={(_, e) => setSelected(e.id)}
          nodesConnectable={false}
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>
      <div className="p-4 text-sm">
        {!decision ? (
          <p className="text-slate-500">Click an edge to see its placement.</p>
        ) : (
          <div className="space-y-2">
            <div className="font-mono text-xs">{decision.relationship_id}</div>
            <Badge value={decision.decision} /> score {(decision.score ?? 0).toFixed(2)}
            <p>{decision.reason}</p>
            <ul className="list-disc pl-5">
              {(decision.top_factors ?? []).map((f) => (
                <li key={f.name}>
                  {f.name}: {f.contribution.toFixed(2)}
                </li>
              ))}
            </ul>
            {decision.ai?.suggestion && (
              <p>
                AI ({decision.ai.source}): <Badge value={decision.ai.suggestion.decision} />{" "}
                {decision.ai.suggestion.justification}{" "}
                {decision.agree != null && (decision.agree ? "✔ agrees" : "✘ disagrees")}
              </p>
            )}
            {onOverride && (
              <div className="flex gap-2">
                {(["EMBED", "REFERENCE"] as const).map((d) => (
                  <button
                    key={d}
                    className="rounded border px-2 py-1 dark:border-slate-600"
                    onClick={() => onOverride(decision.relationship_id, d)}
                  >
                    Force {d}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
