"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

export type ThemeMode = "system" | "light" | "dark";
export type Accent = "lime" | "peach" | "sky" | "lilac" | "rose" | "mint";

export const ACCENTS: { id: Accent; label: string; rgb: string }[] = [
  { id: "lime", label: "Highlighter", rgb: "228 255 92" },
  { id: "peach", label: "Peach", rgb: "255 184 154" },
  { id: "sky", label: "Sky", rgb: "140 203 242" },
  { id: "lilac", label: "Lilac", rgb: "196 176 255" },
  { id: "rose", label: "Rose", rgb: "255 170 205" },
  { id: "mint", label: "Mint", rgb: "130 226 176" },
];

interface ThemeState {
  mode: ThemeMode;
  accent: Accent;
  setMode: (mode: ThemeMode) => void;
  setAccent: (accent: Accent) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

export const MODE_KEY = "atelier-theme";
export const ACCENT_KEY = "atelier-accent";

function apply(mode: ThemeMode, accent: Accent) {
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const root = document.documentElement;
  root.classList.toggle("dark-mode", dark);
  root.setAttribute("data-accent", accent);
}

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = window.localStorage.getItem(key);
    if (v && (allowed as readonly string[]).includes(v)) return v as T;
  } catch {
    // storage unavailable: fall back to the default
  }
  return fallback;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>("system");
  const [accent, setAccentState] = useState<Accent>("lime");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setModeState(readStored(MODE_KEY, ["system", "light", "dark"] as const, "system"));
    setAccentState(
      readStored(ACCENT_KEY, ACCENTS.map((a) => a.id) as readonly Accent[], "lime"),
    );
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    apply(mode, accent);
    if (mode !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system", accent);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode, accent, ready]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      window.localStorage.setItem(MODE_KEY, next);
    } catch {
      // ignore
    }
  }, []);

  const setAccent = useCallback((next: Accent) => {
    setAccentState(next);
    try {
      window.localStorage.setItem(ACCENT_KEY, next);
    } catch {
      // ignore
    }
  }, []);

  return <ThemeContext.Provider value={{ mode, accent, setMode, setAccent }}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
