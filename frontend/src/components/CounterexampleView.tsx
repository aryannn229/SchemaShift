import { RotateCw } from "lucide-react";

import type { Counterexample, CounterexampleGap } from "../api/client";
import { Button, CodeBlock, ErrorState } from "./ui";

/** Minimal counterexample: the data, the operation, and what each database did, side by side. */
export function CounterexampleView({
  example,
  gap,
  onReplay,
  replaying,
  replayError,
}: {
  example?: Counterexample;
  gap?: CounterexampleGap;
  onReplay?: () => void;
  replaying?: boolean;
  replayError?: string | null;
}) {
  if (!example && !gap) return null;
  return (
    <div className="space-y-3 text-xs" data-testid="counterexample">
      <div className="flex items-center gap-2">
        <b>Counterexample</b>
        {example && (
          <span className="text-muted tabular">
            {example.rows_in_setup} row{example.rows_in_setup === 1 ? "" : "s"} of data
            {example.minimal ? ", minimal" : ""} (seed {example.seed})
          </span>
        )}
        {onReplay && (
          <Button
            size="sm"
            className="ml-auto"
            onClick={onReplay}
            disabled={replaying}
            icon={<RotateCw size={12} aria-hidden className={replaying ? "animate-spin" : undefined} />}
          >
            {replaying ? "Replaying…" : "Replay probe"}
          </Button>
        )}
      </div>
      {replayError && <ErrorState message={replayError} title="Replay failed" />}
      {gap && (
        <p className="rounded border border-line bg-bg p-2">
          <b>No counterexample:</b> {gap.reason}
        </p>
      )}
      {example && (
        <>
          <p>{example.description}</p>
          <CodeBlock code={example.setup.join("\n")} title="Setup data" maxHeight="10rem" />
          {example.steps.map((s, i) => (
            <div key={i} className="space-y-2">
              {s.sql && <CodeBlock code={s.sql} title={`Operation ${i + 1}`} maxHeight="8rem" />}
              <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label="Behaviour on each database">
                <div className="rounded border border-line p-2">
                  <div className="font-semibold">PostgreSQL</div>
                  <div>{s.postgres}</div>
                </div>
                <div className="rounded border border-line p-2">
                  <div className="font-semibold">MongoDB</div>
                  <div>{s.mongo}</div>
                </div>
              </div>
            </div>
          ))}
          <p>
            <b>Resulting difference:</b> {example.difference}
          </p>
        </>
      )}
    </div>
  );
}
