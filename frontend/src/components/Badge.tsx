import type { Status } from "../api/client";

const STYLES: Record<string, string> = {
  SAFE: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  MATCH: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  CHANGED: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  BROKEN: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  MISMATCH: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  EMBED: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300",
  REFERENCE: "bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-200",
};

export function Badge({ value }: { value: Status | string }) {
  const cls = STYLES[value] ?? STYLES.REFERENCE;
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ${cls}`}>{value}</span>
  );
}
