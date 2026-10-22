import { useId, useState, type ReactNode } from "react";

export function Tooltip({ text, children }: { text: string; children: ReactNode }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      aria-describedby={open ? id : undefined}
    >
      {children}
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute left-1/2 top-full z-30 mt-1 -translate-x-1/2 whitespace-nowrap rounded border border-line bg-raised px-2 py-1 text-xs text-fg"
        >
          {text}
        </span>
      )}
    </span>
  );
}
