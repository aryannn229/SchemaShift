import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
  const get = () => window.matchMedia?.(query).matches ?? true;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const on = () => setMatches(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return matches;
}
