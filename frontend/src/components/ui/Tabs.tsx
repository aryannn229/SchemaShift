import { useId, type KeyboardEvent, type ReactNode } from "react";

import { cx } from "./cx";

export interface TabItem<T extends string> {
  id: T;
  label: string;
  badge?: ReactNode;
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
  variant = "line",
}: {
  tabs: readonly TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  label?: string;
  variant?: "line" | "pill";
}) {
  const uid = useId();
  const move = (e: KeyboardEvent, i: number) => {
    const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    const next = tabs[(i + delta + tabs.length) % tabs.length];
    onChange(next.id);
    document.getElementById(`${uid}-${next.id}`)?.focus();
  };
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cx("flex flex-wrap gap-0.5", variant === "line" && "border-b border-line")}
    >
      {tabs.map((t, i) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            id={`${uid}-${t.id}`}
            role="tab"
            type="button"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => move(e, i)}
            className={cx(
              "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors",
              variant === "line"
                ? cx(
                    "-mb-px border-b-2",
                    active
                      ? "border-accent text-fg"
                      : "border-transparent text-muted hover:text-fg",
                  )
                : cx("rounded", active ? "bg-raised text-fg" : "text-muted hover:text-fg"),
            )}
          >
            {t.label}
            {t.badge}
          </button>
        );
      })}
    </div>
  );
}
