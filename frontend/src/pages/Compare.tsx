import { GitCompare } from "lucide-react";
import { useState } from "react";

import type { DiffResponse } from "../api/client";
import { useDiff } from "../api/hooks";
import { SqlEditor } from "../components/SqlEditor";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CodeBlock,
  EmptyState,
  ErrorState,
  Table,
  Tabs,
  Td,
  Th,
  THead,
} from "../components/ui";

type Mode = "ddl" | "alter";
type ResultTab = "changes" | "plan" | "guarantees" | "script";

const EXAMPLE_OLD = `CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  fullname TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  age INT
);
CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  total NUMERIC(10,2) NOT NULL
);
`;
const EXAMPLE_NEW = `CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  age BIGINT,
  nickname TEXT DEFAULT 'friend'
);
CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  total NUMERIC(10,2) NOT NULL,
  status TEXT DEFAULT 'new'
);
`;
const EXAMPLE_ALTER = `ALTER TABLE customers RENAME COLUMN fullname TO name;
ALTER TABLE customers DROP CONSTRAINT customers_email_key;
ALTER TABLE customers ALTER COLUMN age TYPE BIGINT;
ALTER TABLE customers ADD COLUMN nickname TEXT DEFAULT 'friend';
ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'new';
`;

const TONE = {
  REVERSIBLE: "safe",
  REVERSIBLE_WITH_SAVED_DATA: "changed",
  IRREVERSIBLE: "broken",
} as const;
const REV_LABEL = {
  REVERSIBLE: "reversible",
  REVERSIBLE_WITH_SAVED_DATA: "reversible with saved data",
  IRREVERSIBLE: "irreversible (lossy)",
} as const;
const DIR_TONE = {
  worse: "broken",
  better: "safe",
  added: "accent",
  removed: "changed",
  same: "neutral",
} as const;

function Changes({
  data,
  confirmed,
  toggle,
}: {
  data: DiffResponse;
  confirmed: Record<string, string>;
  toggle: (key: string, to: string) => void;
}) {
  const pending = (data.diff.rename_candidates ?? []).filter((c) => !c.confirmed);
  return (
    <div className="space-y-4">
      {pending.length > 0 && (
        <Card>
          <CardHeader title="Possible renames (need your confirmation)" />
          <ul className="space-y-2 p-4 text-sm" data-testid="rename-candidates">
            {pending.map((c) => {
              const key = c.kind === "column" ? `${c.table}.${c.old}` : c.old;
              return (
                <li key={key} className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={confirmed[key] === c.new}
                      onChange={() => toggle(key, c.new)}
                    />
                    <span className="font-mono text-xs">
                      {key} to {c.new}
                    </span>
                  </label>
                  <span className="text-xs text-muted">
                    similarity {c.score}. Unconfirmed, this is a drop plus an
                    add and the data is lost.
                  </span>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
      <Table>
        <THead>
          <tr>
            <Th>Change</Th>
            <Th>Table</Th>
            <Th>Description</Th>
          </tr>
        </THead>
        <tbody>
          {data.diff.ops.map((op) => (
            <tr key={op.description} className="border-t border-line">
              <Td className="font-mono text-xs">{op.kind}</Td>
              <Td>{op.table}</Td>
              <Td>{op.description}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {data.diff.ops.length === 0 && (
        <EmptyState title="The two versions are identical" />
      )}
      {(data.warnings ?? []).length > 0 && (
        <ul className="list-disc pl-6 text-sm text-muted">
          {(data.warnings ?? []).map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Plan({ data }: { data: DiffResponse }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      {data.plan_changes.length > 0 && (
        <Card>
          <CardHeader title="Placement changes" />
          <ul className="space-y-1 p-4 text-sm" data-testid="plan-changes">
            {data.plan_changes.map((p) => (
              <li key={p.relationship_id}>
                <span className="font-mono text-xs">{p.relationship_id}</span>:{" "}
                {p.old ?? "none"} to {p.new ?? "none"}
                {p.restructures && (
                  <Badge
                    value="restructures documents"
                    tone="changed"
                    className="ml-2"
                  />
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Table>
        <THead>
          <tr>
            <Th>Step</Th>
            <Th>Action</Th>
            <Th>Needs</Th>
            <Th>Reversibility</Th>
          </tr>
        </THead>
        <tbody>
          {data.steps.map((s) => (
            <tr key={s.id} className="border-t border-line align-top">
              <Td className="font-mono text-xs">{s.id}</Td>
              <Td>
                <button
                  type="button"
                  className="text-left hover:underline"
                  onClick={() => setOpen(open === s.id ? null : s.id)}
                >
                  {s.description}
                </button>
                {open === s.id && (
                  <div className="mt-2 space-y-2 text-xs text-muted">
                    <p>{s.reason}</p>
                    {s.code && (
                      <CodeBlock
                        code={s.code}
                        title="apply"
                        maxHeight="10rem"
                      />
                    )}
                    {s.rollback && (
                      <CodeBlock
                        code={s.rollback}
                        title="rollback"
                        maxHeight="10rem"
                      />
                    )}
                    {s.recovery && <p>Recovery: {s.recovery}</p>}
                    {s.saved_data && <p>Saved data: {s.saved_data}</p>}
                  </div>
                )}
              </Td>
              <Td className="font-mono text-xs">
                {(s.depends_on ?? []).join(", ") || "none"}
              </Td>
              <Td>
                <Badge
                  value={REV_LABEL[s.reversibility]}
                  tone={TONE[s.reversibility]}
                />
                {s.manual && (
                  <Badge value="manual" tone="broken" className="ml-1" />
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {data.steps.length === 0 && <EmptyState title="Nothing to migrate" />}
    </div>
  );
}

function Guarantees({ data }: { data: DiffResponse }) {
  const g = data.guarantees;
  return (
    <div className="space-y-3">
      <p className="text-sm" data-testid="guarantee-summary">
        Overall {g.old_overall} to {g.new_overall}. {g.regressions} worse,{" "}
        {g.improvements} better.
      </p>
      <Table>
        <THead>
          <tr>
            <Th>Guarantee</Th>
            <Th>Rule</Th>
            <Th>Before</Th>
            <Th>After</Th>
            <Th>Why</Th>
          </tr>
        </THead>
        <tbody>
          {g.changes.map((c) => (
            <tr key={c.node_id} className="border-t border-line align-top">
              <Td>
                <Badge value={c.direction} tone={DIR_TONE[c.direction]} />
                <span className="ml-2 font-mono text-xs">{c.node_id}</span>
              </Td>
              <Td className="font-mono text-xs">{c.rule_id}</Td>
              <Td>{c.old ? <Badge value={c.old} /> : "none"}</Td>
              <Td>{c.new ? <Badge value={c.new} /> : "none"}</Td>
              <Td className="text-xs text-muted">
                {(c.because ?? []).join("; ") || c.reason}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {g.changes.length === 0 && <EmptyState title="No guarantee changed" />}
    </div>
  );
}

export default function Compare() {
  const [mode, setMode] = useState<Mode>("alter");
  const [oldSql, setOldSql] = useState(EXAMPLE_OLD);
  const [newSql, setNewSql] = useState(EXAMPLE_NEW);
  const [alterSql, setAlterSql] = useState(EXAMPLE_ALTER);
  const [confirmed, setConfirmed] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<ResultTab>("changes");
  const diff = useDiff();

  const compare = (renames = confirmed) =>
    diff.mutate({
      old_sql: oldSql,
      new_sql: mode === "ddl" ? newSql : "",
      alter_sql: mode === "alter" ? alterSql : "",
      confirmed_renames: renames,
      options: {},
    });
  const toggle = (key: string, to: string) => {
    const next = { ...confirmed };
    if (next[key] === to) delete next[key];
    else next[key] = to;
    setConfirmed(next);
    compare(next);
  };
  const data = diff.data;

  return (
    <div className="mx-auto max-w-screen-2xl space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold">Compare versions</h2>
        <Tabs
          variant="pill"
          label="How the new version is given"
          value={mode}
          onChange={setMode}
          tabs={[
            { id: "alter", label: "ALTER script" },
            { id: "ddl", label: "New schema" },
          ]}
        />
        <Button
          variant="primary"
          onClick={() => compare()}
          icon={<GitCompare size={14} aria-hidden />}
          disabled={diff.isPending}
        >
          {diff.isPending ? "Comparing" : "Compare"}
        </Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="space-y-1">
          <div className="text-xs font-medium text-muted">
            Old schema (version 1)
          </div>
          <div className="h-72 overflow-hidden rounded-lg border border-line">
            <SqlEditor value={oldSql} onChange={setOldSql} label="Old schema" />
          </div>
        </div>
        <div className="space-y-1">
          <div className="text-xs font-medium text-muted">
            {mode === "ddl"
              ? "New schema (version 2)"
              : "ALTER statements applied to version 1"}
          </div>
          <div className="h-72 overflow-hidden rounded-lg border border-line">
            {mode === "ddl" ? (
              <SqlEditor
                value={newSql}
                onChange={setNewSql}
                label="New schema"
              />
            ) : (
              <SqlEditor
                value={alterSql}
                onChange={setAlterSql}
                label="ALTER statements"
              />
            )}
          </div>
        </div>
      </div>
      {diff.error && (
        <ErrorState message={String(diff.error)} title="Could not compare" />
      )}
      {data ? (
        <div className="space-y-3" data-testid="diff-result">
          <Tabs
            label="Comparison results"
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "changes", label: "Changes", badge: data.diff.ops.length },
              { id: "plan", label: "Migration plan", badge: data.steps.length },
              {
                id: "guarantees",
                label: "Guarantee diff",
                badge:
                  data.guarantees.regressions > 0
                    ? `${data.guarantees.regressions} worse`
                    : data.guarantees.changes.length,
              },
              { id: "script", label: "Script" },
            ]}
          />
          {tab === "changes" && (
            <Changes data={data} confirmed={confirmed} toggle={toggle} />
          )}
          {tab === "plan" && <Plan data={data} />}
          {tab === "guarantees" && <Guarantees data={data} />}
          {tab === "script" && (
            <CodeBlock code={data.script} title="evolve.py" maxHeight="32rem" />
          )}
        </div>
      ) : (
        !diff.error && (
          <EmptyState title="Nothing compared yet">
            Edit the two versions, then press Compare. Nothing is executed: the
            comparison only reads the SQL.
          </EmptyState>
        )
      )}
    </div>
  );
}
