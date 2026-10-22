import { useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";

import type { CompileResponse, PlacementDecision } from "../api/client";
import { Badge, Button, Card, CardHeader, EmptyState } from "./ui";

const EMBED_COLOR = "rgb(var(--accent))";
const REF_COLOR = "rgb(var(--muted))";

function Legend() {
  return (
    <ul
      aria-label="Legend"
      className="absolute left-2 top-2 z-10 space-y-1 rounded-lg border border-line bg-surface px-3 py-2 text-xs"
    >
      <li className="flex items-center gap-2">
        <svg width="28" height="8" aria-hidden>
          <line x1="0" y1="4" x2="28" y2="4" stroke={EMBED_COLOR} strokeWidth="2.5" />
        </svg>
        EMBED (solid)
      </li>
      <li className="flex items-center gap-2">
        <svg width="28" height="8" aria-hidden>
          <line x1="0" y1="4" x2="28" y2="4" stroke={REF_COLOR} strokeWidth="2" strokeDasharray="5 4" />
        </svg>
        REFERENCE (dashed)
      </li>
    </ul>
  );
}

export function RelationshipsTab({
  result,
  onOverride,
}: {
  result: CompileResponse;
  onOverride?: (relationshipId: string, decision: "EMBED" | "REFERENCE") => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);

  const nodes: Node[] = useMemo(() => {
    // Layered layout: parents (edge targets) sit above their children.
    const parents = new Map<string, string[]>();
    for (const e of result.graph_edges) {
      if (e.child === e.parent) continue;
      parents.set(e.child, [...(parents.get(e.child) ?? []), e.parent]);
    }
    const depth = new Map<string, number>();
    const level = (id: string, seen: Set<string>): number => {
      if (depth.has(id)) return depth.get(id)!;
      if (seen.has(id)) return 0; // FK cycle: break it here
      seen.add(id);
      const d = Math.max(-1, ...(parents.get(id) ?? []).map((p) => level(p, seen))) + 1;
      depth.set(id, d);
      return d;
    };
    for (const n of result.graph_nodes) level(n.id, new Set());
    const rows = new Map<number, string[]>();
    for (const n of result.graph_nodes) {
      const d = depth.get(n.id) ?? 0;
      rows.set(d, [...(rows.get(d) ?? []), n.id]);
    }
    const pos = new Map<string, { x: number; y: number }>();
    for (const [d, ids] of rows)
      ids.forEach((id, i) => pos.set(id, { x: (i - (ids.length - 1) / 2) * 210, y: d * 110 }));
    return result.graph_nodes.map((n) => ({
      id: n.id,
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      data: { label: `${n.id} (${n.kind})` },
      style: { padding: "8px 12px", fontFamily: "var(--font-mono, ui-monospace)" },
    }));
  }, [result.graph_nodes, result.graph_edges]);
  const edges: Edge[] = useMemo(
    () =>
      result.graph_edges.map((e) => {
        const embed = e.decision === "EMBED";
        return {
          id: e.id,
          source: e.child,
          target: e.parent,
          type: "smoothstep",
          label: e.decision,
          labelStyle: { fontSize: 10, fill: "rgb(var(--fg))" },
          labelBgStyle: { fill: "rgb(var(--surface))" },
          markerEnd: { type: MarkerType.ArrowClosed, color: embed ? EMBED_COLOR : REF_COLOR },
          style: {
            stroke: embed ? EMBED_COLOR : REF_COLOR,
            strokeWidth: embed ? 2.5 : 2,
            strokeDasharray: embed ? undefined : "6 4",
          },
        };
      }),
    [result.graph_edges],
  );

  if (nodes.length === 0) return <EmptyState title="No tables to show" />;
  const decision: PlacementDecision | undefined = result.plan.find(
    (d) => d.relationship_id === selected,
  );

  return (
    <div className="space-y-3 p-4">
      <div
        className="relative h-[30rem] overflow-hidden rounded-lg border border-line"
        data-testid="relationship-graph"
      >
        <Legend />
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.3}
          onEdgeClick={(_, e) => setSelected(e.id)}
          nodesConnectable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="rgb(var(--line))" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable maskColor="rgb(var(--bg) / 0.6)" style={{ width: 110, height: 70 }} />
        </ReactFlow>
      </div>

      <Card>
        <CardHeader title="Placement" />
        <div className="p-4 text-sm">
          {!decision ? (
            <p className="text-muted">Select an edge to see its placement decision.</p>
          ) : (
            <div className="space-y-2">
              <div className="font-mono text-xs text-muted">{decision.relationship_id}</div>
              <div className="flex items-center gap-2">
                <Badge value={decision.decision} />
                <span className="tabular text-muted">score {(decision.score ?? 0).toFixed(2)}</span>
              </div>
              <p>{decision.reason}</p>
              <ul className="list-disc pl-5 text-muted">
                {(decision.top_factors ?? []).map((f) => (
                  <li key={f.name} className="tabular">
                    {f.name}: {f.contribution.toFixed(2)}
                  </li>
                ))}
              </ul>
              {decision.ai?.suggestion && (
                <p>
                  <span className="text-muted">AI ({decision.ai.source}):</span>{" "}
                  <Badge value={decision.ai.suggestion.decision} />{" "}
                  {decision.ai.suggestion.justification}{" "}
                  {decision.agree != null && (
                    <Badge
                      value={decision.agree ? "Agrees" : "Disagrees"}
                      tone={decision.agree ? "safe" : "changed"}
                    />
                  )}
                </p>
              )}
              {onOverride && (
                <div className="flex gap-2 pt-1">
                  {(["EMBED", "REFERENCE"] as const).map((d) => (
                    <Button key={d} size="sm" onClick={() => onOverride(decision.relationship_id, d)}>
                      Force {d}
                    </Button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
