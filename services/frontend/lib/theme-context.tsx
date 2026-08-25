"use client";

import { createContext, useContext, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "cyberpunk";

const VALID_THEMES: Theme[] = ["light", "dark", "cyberpunk"];

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

const STORAGE_KEY = "theme";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("dark");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (VALID_THEMES.includes(stored as Theme)) setThemeState(stored as Theme);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    // "cyberpunk" is a skin layered on top of the dark palette (see
    // styles/cyberpunk.css), not an independent fourth palette — it always
    // carries dark-mode along with it. "light" is genuinely independent.
    root.classList.toggle("dark-mode", theme === "dark" || theme === "cyberpunk");
    root.classList.toggle("light-mode", theme === "light");
    root.classList.toggle("theme-cyberpunk", theme === "cyberpunk");
  }, [theme]);

  function setTheme(next: Theme) {
    setThemeState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
