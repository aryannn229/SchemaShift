import type { HTMLAttributes, ReactNode } from "react";

import { cx } from "./cx";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx("rounded-lg border border-line bg-surface", className)} {...rest} />;
}

export function CardHeader({ title, actions }: { title: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
      <h3 className="text-sm font-semibold">{title}</h3>
      {actions}
    </div>
  );
}
