"use client";

import { createContext, useContext, useEffect, useState } from "react";

export type Theme = "dark" | "cyberpunk";

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
    if (stored === "cyberpunk") setThemeState("cyberpunk");
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("theme-cyberpunk", theme === "cyberpunk");
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
