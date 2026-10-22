import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

import {
  Badge,
  Button,
  CodeBlock,
  Dialog,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  SkeletonBlock,
  StatCard,
  Table,
  Tabs,
  Td,
  Th,
  THead,
  Tooltip,
  VerdictBadge,
} from "../src/components/ui";
import { applyTheme, resolveDark, storedPref } from "../src/lib/theme";
import { titleFor } from "../src/lib/useDocumentTitle";

test("verdict and evidence badges render their labels", () => {
  render(
    <>
      <VerdictBadge status="UNVERIFIED" />
      <EvidenceBadge level="PROVEN" />
      <Badge value="MATCH" />
    </>,
  );
  expect(screen.getByText("UNVERIFIED")).toBeInTheDocument();
  expect(screen.getByText("Proven")).toBeInTheDocument();
  expect(screen.getByText("MATCH")).toBeInTheDocument();
});

test("button forwards clicks and respects disabled", () => {
  const onClick = vi.fn();
  const { rerender } = render(<Button onClick={onClick}>Go</Button>);
  fireEvent.click(screen.getByRole("button", { name: "Go" }));
  expect(onClick).toHaveBeenCalledTimes(1);
  rerender(
    <Button onClick={onClick} disabled>
      Go
    </Button>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Go" }));
  expect(onClick).toHaveBeenCalledTimes(1);
});

function TabsHarness() {
  const [v, setV] = useState<"a" | "b" | "c">("a");
  return (
    <Tabs
      label="demo"
      value={v}
      onChange={setV}
      tabs={[
        { id: "a", label: "A" },
        { id: "b", label: "B" },
        { id: "c", label: "C" },
      ]}
    />
  );
}

test("tabs move with the arrow keys and wrap around", () => {
  render(<TabsHarness />);
  const a = screen.getByRole("tab", { name: "A" });
  fireEvent.keyDown(a, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "B" })).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(screen.getByRole("tab", { name: "B" }), { key: "ArrowLeft" });
  fireEvent.keyDown(screen.getByRole("tab", { name: "A" }), { key: "ArrowLeft" });
  expect(screen.getByRole("tab", { name: "C" })).toHaveAttribute("aria-selected", "true");
});

test("sortable table header reports aria-sort", () => {
  const onSort = vi.fn();
  render(
    <Table>
      <THead>
        <tr>
          <Th sortDir="asc" onSort={onSort}>
            Name
          </Th>
        </tr>
      </THead>
      <tbody>
        <tr>
          <Td>x</Td>
        </tr>
      </tbody>
    </Table>,
  );
  expect(screen.getByRole("columnheader", { name: "Name" })).toHaveAttribute("aria-sort", "ascending");
  fireEvent.click(screen.getByRole("button", { name: "Name" }));
  expect(onSort).toHaveBeenCalled();
});

test("dialog closes on Escape and the close button", () => {
  const onClose = vi.fn();
  render(
    <Dialog open title="Confirm" onClose={onClose}>
      body
    </Dialog>,
  );
  expect(screen.getByRole("dialog", { name: "Confirm" })).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(onClose).toHaveBeenCalledTimes(2);
});

test("tooltip shows on hover", () => {
  render(
    <Tooltip text="Hint">
      <button>target</button>
    </Tooltip>,
  );
  fireEvent.mouseEnter(screen.getByText("target"));
  expect(screen.getByRole("tooltip")).toHaveTextContent("Hint");
});

test("code block copies its content", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  render(<CodeBlock code="db.a.find()" title="q.js" />);
  fireEvent.click(screen.getByRole("button", { name: "Copy q.js" }));
  expect(writeText).toHaveBeenCalledWith("db.a.find()");
  expect(await screen.findByText("Copied")).toBeInTheDocument();
});

test("states and stat cards", () => {
  render(
    <>
      <EmptyState title="Nothing">hint</EmptyState>
      <ErrorState message="API said no" />
      <SkeletonBlock label="Loading things" />
      <StatCard label="Safe" value={3} tone="safe" hint="of 4" />
    </>,
  );
  expect(screen.getByText("Nothing")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("API said no");
  expect(screen.getByRole("status", { name: "Loading things" })).toBeInTheDocument();
  expect(screen.getByText("of 4")).toBeInTheDocument();
});

test("theme preference resolves and persists", () => {
  localStorage.clear();
  expect(storedPref()).toBe("system");
  expect(resolveDark("dark")).toBe(true);
  expect(applyTheme("dark")).toBe(true);
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(storedPref()).toBe("dark");
  applyTheme("light");
  expect(document.documentElement.dataset.theme).toBe("light");
  applyTheme("system");
  expect(storedPref()).toBe("system");
});

test("page titles per route", () => {
  expect(titleFor("/")).toBe("Compile · SchemaShift");
  expect(titleFor("/runs/abc")).toBe("Run report · SchemaShift");
  expect(titleFor("/benchmarks")).toBe("Benchmarks · SchemaShift");
});
