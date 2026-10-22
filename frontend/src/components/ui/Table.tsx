import { ArrowDown, ArrowUp } from "lucide-react";
import type { ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";

import { cx } from "./cx";

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div tabIndex={0} className={cx("overflow-auto rounded-lg border border-line", className)}>
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="sticky top-0 z-10 bg-surface text-xs text-muted">{children}</thead>;
}

export function Th({
  children,
  sortDir,
  onSort,
  className,
  ...rest
}: ThHTMLAttributes<HTMLTableCellElement> & {
  sortDir?: "asc" | "desc" | null;
  onSort?: () => void;
}) {
  return (
    <th
      scope="col"
      aria-sort={sortDir ? (sortDir === "asc" ? "ascending" : "descending") : undefined}
      className={cx("border-b border-line px-3 py-2 font-medium", className)}
      {...rest}
    >
      {onSort ? (
        <button
          type="button"
          onClick={onSort}
          className="inline-flex items-center gap-1 font-medium hover:text-fg"
        >
          {children}
          {sortDir === "asc" && <ArrowUp size={12} aria-hidden />}
          {sortDir === "desc" && <ArrowDown size={12} aria-hidden />}
        </button>
      ) : (
        children
      )}
    </th>
  );
}

export function Td({ className, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cx("border-b border-line px-3 py-2 align-top", className)} {...rest} />;
}
