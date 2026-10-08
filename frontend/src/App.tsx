import { useEffect, useState } from "react";
import { NavLink, Outlet, Route, Routes } from "react-router-dom";

import { applyTheme, initialDark } from "./lib/theme";
import About from "./pages/About";
import History from "./pages/History";
import Home from "./pages/Home";
import RunPage from "./pages/RunPage";

function Layout() {
  const [dark, setDark] = useState(initialDark);
  useEffect(() => applyTheme(dark), [dark]);
  const link = ({ isActive }: { isActive: boolean }) =>
    isActive ? "font-semibold underline" : "text-slate-600 dark:text-slate-300";
  return (
    <div className="min-h-screen bg-white text-slate-900 dark:bg-slate-900 dark:text-slate-100">
      <header className="flex items-center gap-4 border-b px-4 py-2 dark:border-slate-700">
        <h1 className="text-lg font-bold">SchemaShift</h1>
        <nav className="flex gap-3 text-sm">
          <NavLink to="/" end className={link}>
            Compile
          </NavLink>
          <NavLink to="/history" className={link}>
            History
          </NavLink>
          <NavLink to="/about" className={link}>
            About
          </NavLink>
        </nav>
        <button
          aria-label="Toggle dark mode"
          className="ml-auto rounded border px-2 py-1 text-sm dark:border-slate-600"
          onClick={() => setDark(!dark)}
        >
          {dark ? "Light" : "Dark"}
        </button>
      </header>
      <main>
        <Outlet context={{ dark }} />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="runs/:id" element={<RunPage />} />
        <Route path="history" element={<History />} />
        <Route path="about" element={<About />} />
      </Route>
    </Routes>
  );
}
