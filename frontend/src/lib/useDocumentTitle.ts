import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const TITLES: [RegExp, string][] = [
  [/^\/runs\//, "Run report"],
  [/^\/history/, "History"],
  [/^\/benchmarks/, "Benchmarks"],
  [/^\/about/, "About"],
  [/^\//, "Compile"],
];

export function titleFor(pathname: string): string {
  const hit = TITLES.find(([re]) => re.test(pathname));
  return `${hit ? hit[1] : "Compile"} · SchemaShift`;
}

/** Sets a consistent page title per route. */
export function useDocumentTitle(): void {
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = titleFor(pathname);
  }, [pathname]);
}
