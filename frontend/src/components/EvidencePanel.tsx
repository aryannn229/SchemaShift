import type { EvidenceUpgrade, Verdict } from "../api/client";
import { EvidenceBadge, type EvidenceLevel } from "./ui";

export function effectiveLevel(v: Verdict, upgrade?: EvidenceUpgrade): EvidenceLevel {
  return (upgrade?.level ?? v.evidence ?? "UNVERIFIED") as EvidenceLevel;
}

const MEANING: Record<EvidenceLevel, string> = {
  PROVEN: "Established by a stated argument; no run is needed.",
  CONDITIONAL: "Holds only while the conditions below hold.",
  TESTED: "Supported by runs on real databases, not proven.",
  UNVERIFIED: "No evidence either way yet. Run verification to test it.",
};

/** What the verdict rests on: the level, the argument or conditions, and what decided it. */
export function EvidencePanel({ verdict, upgrade }: { verdict: Verdict; upgrade?: EvidenceUpgrade }) {
  const level = effectiveLevel(verdict, upgrade);
  return (
    <div className="space-y-3 text-xs" data-testid="evidence-panel">
      <div className="flex flex-wrap items-center gap-2">
        <b>Evidence</b>
        <EvidenceBadge level={level} />
        <span className="text-muted">{MEANING[level]}</span>
      </div>
      {verdict.argument && (
        <p>
          <b>Argument:</b> {verdict.argument}.
        </p>
      )}
      {(verdict.evidence_conditions ?? []).length > 0 && (
        <div>
          <b>Conditions</b>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {(verdict.evidence_conditions ?? []).map((c, i) => (
              <li key={i}>
                <span className="font-mono">{c.kind}</span>: {c.text}
              </li>
            ))}
          </ul>
        </div>
      )}
      {upgrade && (
        <p>
          <b>Tested:</b> {upgrade.basis}.
        </p>
      )}
      {verdict.decided_by && (
        <div>
          <b>Decided by</b> <span className="font-mono">{verdict.decided_by}</span>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted">
            {(verdict.inputs ?? []).map((i, k) => (
              <li key={k}>
                {i.name}: <span className="font-mono">{i.value}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
