import type { ConcurrencyResult, ConcurrencySkip } from "../api/client";
import { Badge, Table, Td, Th, THead } from "./ui";

/** What the concurrency probes observed for one scenario: shipped helpers vs the same without the guard. */
export function ConcurrencyEvidence({
  results,
  skip,
}: {
  results: ConcurrencyResult[];
  skip?: ConcurrencySkip;
}) {
  if (results.length === 0 && !skip) return null;
  return (
    <div className="space-y-2 text-xs" data-testid="concurrency-evidence">
      <b>Concurrency probes</b>
      {skip && (
        <p className="rounded border border-line bg-bg p-2">
          <b>Not probed:</b> {skip.reason}
        </p>
      )}
      {results.length > 0 && (
        <>
          <Table>
            <THead>
              <tr>
                <Th>Strategy</Th>
                <Th>Writers</Th>
                <Th>Rounds</Th>
                <Th>Violations</Th>
                <Th>What counts</Th>
                <Th>Seed</Th>
              </tr>
            </THead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i}>
                  <Td>
                    <Badge
                      value={r.strategy}
                      tone={r.strategy === "generated" ? "accent" : "neutral"}
                    />
                  </Td>
                  <Td className="tabular">{r.threads}</Td>
                  <Td className="tabular">{r.iterations}</Td>
                  <Td className="tabular">
                    <Badge
                      value={`${r.violations} of ${r.iterations}`}
                      tone={r.violations === 0 ? "safe" : "broken"}
                    />
                  </Td>
                  <Td>{r.violation}</Td>
                  <Td className="tabular">{r.seed}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <p className="text-muted">
            <b>generated</b> runs the helpers as shipped; <b>unguarded</b> removes the guard to show what
            the same operations would let through. Zero violations is test evidence, not proof.{" "}
            {results[0]?.detail}
          </p>
        </>
      )}
    </div>
  );
}
