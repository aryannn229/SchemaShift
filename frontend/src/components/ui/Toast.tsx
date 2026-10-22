import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

import { cx } from "./cx";

interface ToastItem {
  id: number;
  message: string;
  tone: "info" | "error";
}

const ToastContext = createContext<(message: string, tone?: ToastItem["tone"]) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((message: string, tone: ToastItem["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, message, tone }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4000);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div aria-live="polite" className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            className={cx(
              "rounded border px-3 py-2 text-sm",
              t.tone === "error"
                ? "border-broken/40 bg-broken-soft text-broken"
                : "border-line bg-raised text-fg",
            )}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
