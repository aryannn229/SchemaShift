import { fireEvent, render, screen } from "@testing-library/react";

import type { CompileResponse } from "../src/api/client";
import { OverviewTab } from "../src/components/OverviewTab";

const result = {
  overall_verdict: "BROKEN",
  counts: { SAFE: 4, CHANGED: 2, BROKEN: 1 },
  diagnostics: [],
  verdicts: [
    { node_id: "cascade_delete:t.a->a.id", rule_id: "EQ-CASCADE-DEL", status: "BROKEN", reason: "orphans", mitigation: "delete children" },
    { node_id: "aggregate:q1", rule_id: "EQ-AGG", status: "CHANGED", reason: "sum differs", mitigation: "wrap in cond" },
  ],
  queries: [
    { query_id: "q1", sql: "SELECT SUM(x) FROM t", kind: "SELECT", error: null, mongosh: "db.t.aggregate([])" },
  ],
} as unknown as CompileResponse;

test("overview explains problems in plain words and shows the diagram", () => {
  render(<OverviewTab result={result} />);
  expect(screen.getByTestId("overall-verdict")).toHaveTextContent("BROKEN");
  expect(screen.getByRole("img", { name: /1 will break/ })).toBeInTheDocument();
  fireEvent.click(screen.getByText("Deleting a parent leaves its children behind"));
  expect(screen.getByText("How to fix")).toBeInTheDocument();
});

test("overview links a query to its issue and shows the fixed MongoDB code", () => {
  render(<OverviewTab result={result} />);
  fireEvent.click(screen.getByText("SELECT SUM(x) FROM t"));
  expect(screen.getByText(/Fix:/)).toBeInTheDocument();
  expect(screen.getByText(/db\.t\.aggregate/)).toBeInTheDocument();
});
