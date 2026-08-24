"use client";

import { Zap } from "@untitledui/icons";
import { useTheme } from "@/lib/theme-context";
import { Button } from "@/components/base/buttons/button";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const isCyberpunk = theme === "cyberpunk";

  return (
    <Button
      color="tertiary"
      size="sm"
      iconLeading={Zap}
      onPress={() => setTheme(isCyberpunk ? "dark" : "cyberpunk")}
      className={isCyberpunk ? "text-brand-secondary" : undefined}
      aria-label={isCyberpunk ? "Switch to standard theme" : "Switch to cyberpunk theme"}
    >
      <span className="hidden sm:inline">{isCyberpunk ? "Cyberpunk" : "Standard"}</span>
    </Button>
  );
}
