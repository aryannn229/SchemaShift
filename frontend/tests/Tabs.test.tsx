import { fireEvent, render, screen } from "@testing-library/react";

import type { CompileResponse } from "../src/api/client";
import { Badge } from "../src/components/Badge";
import { SummaryTab } from "../src/components/SummaryTab";
import { VerdictsTab } from "../src/components/VerdictsTab";

const verdict = (id: string, status: "SAFE" | "CHANGED" | "BROKEN") => ({
  node_id: id,
  rule_id: "R1",
  ir_node_type: "Unique",
  status,
  outcome: "x",
  reason: `reason ${id}`,
  mitigation: null,
  conditions_evaluated: [{ condition: "c", result: true, detail: "d" }],
});

const result = {
  overall_verdict: "BROKEN",
  counts: { SAFE: 1, CHANGED: 1, BROKEN: 1 },
  diagnostics: [{ severity: "warning", code: "SEM001", message: "careful" }],
  verdicts: [verdict("a", "SAFE"), verdict("b", "BROKEN"), verdict("c", "CHANGED")],
} as unknown as CompileResponse;

test("summary shows verdict and diagnostics", () => {
  render(<SummaryTab result={result} />);
  expect(screen.getByTestId("overall-verdict")).toHaveTextContent("BROKEN");
  expect(screen.getByText("careful", { exact: false })).toBeInTheDocument();
});

test("verdicts sort BROKEN first, filter and expand Why", () => {
  render(<VerdictsTab result={result} />);
  const cells = () => screen.getAllByText(/^reason /).map((e) => e.textContent);
  expect(cells()).toEqual(["reason b", "reason c", "reason a"]);
  fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "SAFE" } });
  expect(cells()).toEqual(["reason a"]);
  fireEvent.click(screen.getByText("reason a"));
  expect(screen.getByText("Why?")).toBeInTheDocument();
});

test("badge renders its value", () => {
  render(<Badge value="MATCH" />);
  expect(screen.getByText("MATCH")).toBeInTheDocument();
});
