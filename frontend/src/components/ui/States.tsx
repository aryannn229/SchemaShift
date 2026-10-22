import { AlertTriangle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
  title,
  children,
  action,
}: {
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      <Inbox size={20} className="text-muted" aria-hidden />
      {title && <p className="text-sm font-medium">{title}</p>}
      {children && <p className="max-w-sm text-sm text-muted">{children}</p>}
      {action}
    </div>
  );
}

/** Shows the API's readable message, never a stack trace. */
export function ErrorState({
  message,
  title = "Something went wrong",
}: {
  message: string;
  title?: string;
}) {
  return (
    <div
      role="alert"
      className="m-4 flex gap-3 rounded-lg border border-broken/40 bg-broken-soft p-3 text-sm text-broken"
    >
      <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
      <div>
        <p className="font-medium">{title}</p>
        <p>{message}</p>
      </div>
    </div>
  );
}
