export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="p-6 text-center text-sm text-slate-500">{children}</p>;
}

export function Loading({ label = "Working…" }: { label?: string }) {
  return (
    <p role="status" className="p-6 text-center text-sm text-slate-500">
      {label}
    </p>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="m-4 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
    >
      {message}
    </p>
  );
}
