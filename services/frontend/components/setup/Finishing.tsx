"use client";

import { useEffect, useState } from "react";
import { waitForRestart } from "@/lib/setup";
import { PILL_PRIMARY } from "./SetupShell";

export function Finishing() {
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [pct, setPct] = useState(8);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    waitForRestart()
      .then(() => {
        // Full reload so auth and setup state are fetched fresh.
        if (!cancelled) window.location.assign("/connections/");
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Something went wrong.");
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // Cosmetic progress: eases toward 90% while we wait.
  useEffect(() => {
    if (error) return;
    const id = setInterval(() => setPct((p) => p + (90 - p) * 0.08), 500);
    return () => clearInterval(id);
  }, [error]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-[26px] bg-ink-950 px-6">
      <h1 className="text-center font-serif text-[44px] leading-[1.05] tracking-[-0.01em] text-ink-100 md:text-[56px]">
        You&apos;re <span className="hl italic px-1.5">all set</span>.
      </h1>
      {error ? (
        <>
          <p role="alert" className="max-w-md text-center text-sm text-error-primary">
            {error}
          </p>
          <button type="button" className={PILL_PRIMARY} onClick={() => setAttempt((a) => a + 1)}>
            Try again
          </button>
        </>
      ) : (
        <>
          <div className="h-1 w-[260px] overflow-hidden rounded-full bg-ink-800">
            <div className="h-1 rounded-full bg-ink-100 transition-[width] duration-500" style={{ width: `${pct}%` }} />
          </div>
          <p role="status" className="font-mono text-xs tracking-[0.08em] text-ink-500">
            FINISHING SETUP… A FEW SECONDS
          </p>
        </>
      )}
    </div>
  );
}
