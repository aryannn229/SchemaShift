import type { ReactNode } from "react";

import { cx } from "./cx";

export function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "safe" | "changed" | "broken";
}) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div
        className={cx(
          "tabular text-2xl font-semibold",
          tone === "safe" && "text-safe",
          tone === "changed" && "text-changed",
          tone === "broken" && "text-broken",
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}
