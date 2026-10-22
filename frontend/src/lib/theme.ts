export type ThemePref = "system" | "light" | "dark";
const KEY = "schemashift-theme";

export function storedPref(): ThemePref {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

export function systemDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function resolveDark(pref: ThemePref): boolean {
  return pref === "system" ? systemDark() : pref === "dark";
}

export function applyTheme(pref: ThemePref): boolean {
  const dark = resolveDark(pref);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.classList.toggle("dark", dark);
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    /* ignore */
  }
  return dark;
}
