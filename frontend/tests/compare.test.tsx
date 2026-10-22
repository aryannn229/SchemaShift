import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import Compare from "../src/pages/Compare";

const store = vi.hoisted(() => ({ calls: [] as unknown[], data: undefined as unknown }));

const response = {
  diff: {
    ops: [
      { kind: "ADD_COLUMN", table: "users", column: "nick", detail: {}, description: "add column users.nick TEXT" },
      { kind: "DROP_COLUMN", table: "users", column: "fullname", detail: {}, description: "drop column users.fullname" },
    ],
    rename_candidates: [
      { kind: "column", table: "users", old: "fullname", new: "name", score: 0.67, confirmed: false },
    ],
    warnings: [],
  },
  plan_changes: [
    { relationship_id: "fk:orders.user_id->users.id", old: "EMBED", new: "REFERENCE", restructures: true, reason: "x" },
  ],
  steps: [
    {
      id: "s1",
      kind: "UNSET_FIELD",
      collection: "users",
      description: "remove field fullname from users",
      depends_on: [],
      reversibility: "REVERSIBLE_WITH_SAVED_DATA",
      reason: "values are copied first",
      code: "evolve_runtime.unset_field(db, 'users', [], 'fullname')",
      rollback: "evolve_runtime.restore_top(db, 'users', 'fullname', 's1')",
      saved_data: "_evolve_backup",
      recovery: null,
      manual: false,
      changes: [],
    },
    {
      id: "s2",
      kind: "MANUAL",
      collection: "users",
      description: "change primary key of users",
      depends_on: ["s1"],
      reversibility: "IRREVERSIBLE",
      reason: "re-keys every document",
      code: "",
      rollback: null,
      recovery: "Take a snapshot first",
      manual: true,
      changes: [],
    },
  ],
  guarantees: {
    old_overall: "SAFE",
    new_overall: "CHANGED",
    regressions: 1,
    improvements: 0,
    changes: [
      {
        node_id: "unique:users.email",
        rule_id: "EQ-VALUE-UNIQ",
        old: "SAFE",
        new: null,
        direction: "removed",
        because: ["drop UNIQUE (email) on users"],
        reason: "gone",
      },
    ],
  },
  warnings: [],
  script: "def apply(db): pass",
  old_diagnostics: [],
  new_diagnostics: [],
};

vi.mock("../src/api/hooks", () => ({
  useDiff: () => ({
    data: store.data,
    error: null,
    isPending: false,
    mutate: (body: unknown) => store.calls.push(body),
  }),
}));

describe("compare versions page", () => {
  const open = () =>
    render(
      <MemoryRouter>
        <Compare />
      </MemoryRouter>,
    );

  it("sends both versions and the chosen mode", () => {
    store.data = undefined;
    open();
    fireEvent.click(screen.getByRole("button", { name: /^Compare/ }));
    const sent = store.calls[store.calls.length - 1] as { old_sql: string; alter_sql: string; new_sql: string };
    expect(sent.old_sql).toContain("CREATE TABLE customers");
    expect(sent.alter_sql).toContain("ALTER TABLE");
    expect(sent.new_sql).toBe("");
    expect(screen.getByText("Nothing compared yet")).toBeTruthy();
  });

  it("asks before treating a drop plus an add as a rename, and re-compares when confirmed", () => {
    store.data = response;
    open();
    expect(screen.getByText(/Possible renames/)).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    const sent = store.calls[store.calls.length - 1] as { confirmed_renames: Record<string, string> };
    expect(sent.confirmed_renames).toEqual({ "users.fullname": "name" });
  });

  it("shows steps with their reversibility, the placement change and the guarantee diff", () => {
    store.data = response;
    open();
    fireEvent.click(screen.getByRole("tab", { name: /Migration plan/ }));
    expect(screen.getByText("reversible with saved data")).toBeTruthy();
    expect(screen.getByText("irreversible (lossy)")).toBeTruthy();
    expect(screen.getByTestId("plan-changes").textContent).toContain("EMBED to REFERENCE");
    fireEvent.click(screen.getByText("remove field fullname from users"));
    expect(screen.getByText(/values are copied first/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /Guarantee diff/ }));
    expect(screen.getByTestId("guarantee-summary").textContent).toContain("1 worse");
    expect(screen.getByText("unique:users.email")).toBeTruthy();
  });
});
