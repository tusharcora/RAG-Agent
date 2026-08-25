"use client";

import { useTheme, type Theme } from "@/lib/theme-context";

const OPTIONS: { value: Theme; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "cyberpunk", label: "Cyberpunk" },
];

// A proper 3-way segmented control, for the Settings page — the compact
// single-click cycle used in Nav.tsx's dropdown item doesn't fit here since
// this is a real settings control, not a quick nav action.
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="inline-flex rounded-full border border-ink-700 bg-ink-950/60 p-0.5 text-xs">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => setTheme(opt.value)}
          aria-pressed={theme === opt.value}
          className={`rounded-full px-3 py-1.5 font-medium transition ${
            theme === opt.value ? "bg-coral-500 text-white" : "text-ink-400 hover:text-ink-200"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
