import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { CompileResponse, RunDetail } from "../src/api/client";
import { CounterexampleView } from "../src/components/CounterexampleView";
import { EvidencePanel, effectiveLevel } from "../src/components/EvidencePanel";
import { VerdictsTab } from "../src/components/VerdictsTab";

type V = CompileResponse["verdicts"][number];

const replayCalls = vi.hoisted(() => [] as [string | undefined, string][]);

// The generated client captures fetch at import time, so the replay hook is replaced instead.
vi.mock("../src/api/hooks", async () => {
  const React = await import("react");
  return {
    useReplayCounterexample: (runId: string | undefined) => {
      const [data, setData] = React.useState<unknown>(undefined);
      return {
        data,
        isPending: false,
        error: null,
        mutate: (verdictId: string) => {
          replayCalls.push([runId, verdictId]);
          setData({
            verdict_id: verdictId,
            gap: null,
            counterexample: {
              verdict_id: verdictId,
              description: "replayed",
              setup: [],
              steps: [{ description: "d", sql: null, postgres: "p", mongo: "m" }],
              difference: "same",
              rows_in_setup: 1,
              minimal: true,
              seed: 9,
            },
          });
        },
      };
    },
  };
});

const base = {
  node_id: "cascade_delete:o.u->u.id",
  rule_id: "EQ-CASCADE-DEL",
  ir_node_type: "CascadingDelete",
  status: "BROKEN",
  outcome: "REFERENCED",
  reason: "no native cascade",
  mitigation: null,
  conditions_evaluated: [],
  evidence: "PROVEN",
  argument: "MongoDB has no cascade",
  evidence_conditions: [],
  decided_by: "EQ-CASCADE-DEL/REFERENCED: child stored by reference",
  inputs: [{ name: "placement of fk:o.u->u.id", value: "REFERENCE (optimizer score 0.31)" }],
} as unknown as V;

const conditional = {
  ...base,
  node_id: "fk:o.u->u.id",
  status: "CHANGED",
  reason: "no foreign keys",
  evidence: "CONDITIONAL",
  argument: null,
  evidence_conditions: [
    { kind: "app_enforcement", text: "writes go through the helpers", parameters: {} },
  ],
} as unknown as V;

const unverified = {
  ...base,
  node_id: "join:q1#0",
  status: "SAFE",
  reason: "join ok",
  evidence: "UNVERIFIED",
  argument: null,
} as unknown as V;

const example = {
  verdict_id: base.node_id,
  description: "delete users(id=1), which still has orders rows",
  setup: [
    'INSERT INTO "users" ("id") VALUES (1);',
    'INSERT INTO "orders" ("id", "user_id") VALUES (1, 1);',
  ],
  steps: [
    {
      description: "delete",
      sql: 'DELETE FROM "users" WHERE "id" = 1',
      postgres: "statement succeeded (1 row(s) affected)",
      mongo: "deleted 1 parent document (no cascade, no checks)",
    },
  ],
  difference: "orders: PostgreSQL 0 row(s) vs MongoDB 1",
  tables_differing: ["orders"],
  rows_in_setup: 2,
  minimal: true,
  reproducible: true,
  seed: 1,
};

test("evidence panel shows the argument, the decision and its inputs", () => {
  render(<EvidencePanel verdict={base} />);
  expect(screen.getByText("Proven")).toBeInTheDocument();
  expect(screen.getByText(/MongoDB has no cascade/)).toBeInTheDocument();
  expect(screen.getByText(/EQ-CASCADE-DEL\/REFERENCED/)).toBeInTheDocument();
  expect(screen.getByText(/REFERENCE \(optimizer score 0.31\)/)).toBeInTheDocument();
});

test("evidence panel lists structured conditions", () => {
  render(<EvidencePanel verdict={conditional} />);
  expect(screen.getByText("Conditional")).toBeInTheDocument();
  expect(screen.getByText("app_enforcement")).toBeInTheDocument();
  expect(screen.getByText(/writes go through the helpers/)).toBeInTheDocument();
});

test("a verification upgrade shows TESTED with its basis", () => {
  const upgrade = {
    verdict_id: "join:q1#0",
    level: "TESTED",
    basis: "query q1 returned the same result",
  } as never;
  expect(effectiveLevel(unverified)).toBe("UNVERIFIED");
  expect(effectiveLevel(unverified, upgrade)).toBe("TESTED");
  render(<EvidencePanel verdict={unverified} upgrade={upgrade} />);
  expect(screen.getByText("Tested")).toBeInTheDocument();
  expect(screen.getByText(/query q1 returned the same result/)).toBeInTheDocument();
});

test("counterexample view shows setup, operation and both outcomes side by side", () => {
  const onReplay = vi.fn();
  render(<CounterexampleView example={example as never} onReplay={onReplay} />);
  expect(screen.getByText(/2 rows of data, minimal/)).toBeInTheDocument();
  expect(screen.getByText(/DELETE FROM/)).toBeInTheDocument();
  const group = screen.getByRole("group", { name: "Behaviour on each database" });
  expect(group).toHaveTextContent("PostgreSQL");
  expect(group).toHaveTextContent("no cascade");
  expect(screen.getByText(/orders: PostgreSQL 0 row\(s\) vs MongoDB 1/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Replay probe" }));
  expect(onReplay).toHaveBeenCalledTimes(1);
});

test("a documented gap is shown instead of a counterexample", () => {
  render(<CounterexampleView gap={{ verdict_id: "x", reason: "a representation property" }} />);
  expect(screen.getByText(/No counterexample/)).toBeInTheDocument();
  expect(screen.getByText(/a representation property/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Replay/ })).toBeNull();
});

test("counterexample view renders nothing without data and shows replay errors", () => {
  const { container, rerender } = render(<CounterexampleView />);
  expect(container).toBeEmptyDOMElement();
  rerender(
    <CounterexampleView
      example={example as never}
      replayError="sandbox down"
      onReplay={() => {}}
      replaying
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("sandbox down");
  expect(screen.getByRole("button", { name: "Replaying…" })).toBeDisabled();
});

const result = { verdicts: [base, conditional, unverified] } as unknown as CompileResponse;

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
}

test("verdicts table has an evidence column and filter", () => {
  wrap(<VerdictsTab result={result} />);
  expect(screen.getAllByText("Proven").length).toBeGreaterThan(0);
  expect(screen.getByText("Unverified")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Evidence"), { target: { value: "CONDITIONAL" } });
  expect(screen.queryByText("no native cascade")).toBeNull();
  expect(screen.getByText("no foreign keys")).toBeInTheDocument();
});

test("without verification the details point to running it", () => {
  wrap(<VerdictsTab result={result} />);
  fireEvent.click(screen.getByText("no native cascade"));
  expect(screen.getByTestId("evidence-panel")).toBeInTheDocument();
  expect(
    screen.getByText(/Run verification to generate a minimal counterexample/),
  ).toBeInTheDocument();
});

test("with a verified run the stored counterexample is shown and can be replayed", async () => {
  const run = {
    id: "run-1",
    status: "done",
    verification: { counterexamples: [example], counterexample_gaps: [], evidence_upgrades: [] },
  } as unknown as RunDetail;
  wrap(<VerdictsTab result={result} run={run} />);
  fireEvent.click(screen.getByText("no native cascade"));
  expect(screen.getByTestId("counterexample")).toBeInTheDocument();
  expect(screen.getByText(/seed 1/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Replay probe" }));
  await waitFor(() => expect(screen.getByText(/seed 9/)).toBeInTheDocument());
  expect(replayCalls).toEqual([["run-1", base.node_id]]);
});
