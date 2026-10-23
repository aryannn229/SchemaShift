import { Monitor, Moon, Sun } from "lucide-react";
import { NavLink, Outlet, Route, Routes } from "react-router-dom";

import { ToastProvider, cx } from "./components/ui";
import { useTheme } from "./lib/ThemeContext";
import { useDocumentTitle } from "./lib/useDocumentTitle";
import About from "./pages/About";
import Benchmarks from "./pages/Benchmarks";
import Compare from "./pages/Compare";
import History from "./pages/History";
import Home from "./pages/Home";
import RunPage from "./pages/RunPage";

function GithubMark() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.5 7.5 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

const REPO_URL = "https://github.com/aryannn229/SchemaShift";

const NAV = [
  { to: "/", label: "Compile", end: true },
  { to: "/compare", label: "Compare versions", end: false },
  { to: "/history", label: "History", end: false },
  { to: "/benchmarks", label: "Benchmarks", end: false },
  { to: "/about", label: "About", end: false },
] as const;

function ThemeToggle() {
  const { pref, setPref } = useTheme();
  const next = pref === "system" ? "light" : pref === "light" ? "dark" : "system";
  const Icon = pref === "system" ? Monitor : pref === "light" ? Sun : Moon;
  return (
    <button
      type="button"
      aria-label={`Theme: ${pref}. Switch to ${next}`}
      title={`Theme: ${pref}`}
      onClick={() => setPref(next)}
      className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-line text-muted shadow-sm hover:bg-raised hover:text-fg"
    >
      <Icon size={15} aria-hidden />
    </button>
  );
}

function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-accent focus:px-3 focus:py-1.5 focus:text-accent-fg"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-line bg-bg/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-screen-2xl items-center gap-8 px-4">
          <NavLink to="/" className="flex items-baseline gap-1.5">
            <h1 className="text-lg font-semibold tracking-tight">SchemaShift</h1>
            <span className="hidden text-xs text-muted sm:inline">SQL to MongoDB migration</span>
          </NavLink>
          <nav aria-label="Primary" className="flex items-center gap-1">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  cx(
                    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    isActive ? "bg-accent-soft text-accent" : "text-muted hover:bg-raised hover:text-fg",
                  )
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line px-3 text-sm text-muted shadow-sm hover:bg-raised hover:text-fg"
            >
              <GithubMark />
              <span className="hidden sm:inline">GitHub</span>
              <span className="sr-only sm:hidden">GitHub repository</span>
            </a>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main id="main" className="flex-1">
        <Outlet />
      </main>
      <footer className="border-t border-line px-4 py-3 text-xs text-muted">
        <div className="mx-auto max-w-screen-2xl">
          Do not paste confidential schemas: compiled runs are stored so they can be shared by link,
          and schema structure (never seed data) may be sent to an AI model for advisory opinions.
        </div>
      </footer>
    </div>
  );
}

export default function App() {
  useDocumentTitle();
  return (
    <ToastProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="runs/:id" element={<RunPage />} />
          <Route path="compare" element={<Compare />} />
          <Route path="history" element={<History />} />
          <Route path="benchmarks" element={<Benchmarks />} />
          <Route path="about" element={<About />} />
        </Route>
      </Routes>
    </ToastProvider>
  );
}
