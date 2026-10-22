import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { applyTheme, storedPref, systemDark, type ThemePref } from "./theme";

interface ThemeValue {
  pref: ThemePref;
  dark: boolean;
  setPref: (p: ThemePref) => void;
}

const ThemeContext = createContext<ThemeValue>({ pref: "system", dark: false, setPref: () => {} });

export function useTheme(): ThemeValue {
  return useContext(ThemeContext);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPref] = useState<ThemePref>(storedPref);
  const [dark, setDark] = useState(() => (pref === "system" ? systemDark() : pref === "dark"));

  useEffect(() => setDark(applyTheme(pref)), [pref]);
  useEffect(() => {
    if (pref !== "system" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setDark(applyTheme("system"));
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, [pref]);

  const value = useMemo(() => ({ pref, dark, setPref }), [pref, dark]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
