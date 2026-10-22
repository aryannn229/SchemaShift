import { cx } from "./cx";

export type VerdictValue = "SAFE" | "CHANGED" | "BROKEN" | "UNVERIFIED";
export type EvidenceLevel = "PROVEN" | "CONDITIONAL" | "TESTED" | "UNVERIFIED";

const TONES = {
  safe: "bg-safe-soft text-safe",
  changed: "bg-changed-soft text-changed",
  broken: "bg-broken-soft text-broken",
  neutral: "bg-unverified-soft text-unverified",
  accent: "bg-accent-soft text-accent",
} as const;

const VALUE_TONE: Record<string, keyof typeof TONES> = {
  SAFE: "safe",
  MATCH: "safe",
  DONE: "safe",
  CHANGED: "changed",
  BROKEN: "broken",
  MISMATCH: "broken",
  FAILED: "broken",
  EMBED: "accent",
  UNVERIFIED: "neutral",
};

export function Badge({
  value,
  tone,
  className,
}: {
  value: string;
  tone?: keyof typeof TONES;
  className?: string;
}) {
  const t = tone ?? VALUE_TONE[value.toUpperCase()] ?? "neutral";
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-sm px-1.5 py-0.5 text-xs font-semibold tracking-wide",
        TONES[t],
        className,
      )}
    >
      {value}
    </span>
  );
}

/** Verdict status (SAFE / CHANGED / BROKEN / UNVERIFIED). */
export function VerdictBadge({ status }: { status: VerdictValue | string }) {
  return <Badge value={status} />;
}

const EVIDENCE_LABEL: Record<EvidenceLevel, string> = {
  PROVEN: "Proven",
  CONDITIONAL: "Conditional",
  TESTED: "Tested",
  UNVERIFIED: "Unverified",
};

/** Evidence level, rendered with an outline style so it reads as secondary to the verdict. */
export function EvidenceBadge({ level }: { level: EvidenceLevel }) {
  return (
    <span
      className="inline-flex items-center rounded-sm border border-line px-1.5 py-0.5 text-xs font-medium text-muted"
      title={`Evidence: ${EVIDENCE_LABEL[level]}`}
    >
      {EVIDENCE_LABEL[level]}
    </span>
  );
}
