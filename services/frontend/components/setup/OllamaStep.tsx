"use client";

import { ArrowUpRight } from "@untitledui/icons";
import { getSetupStatus } from "@/lib/setup";
import { useInterval } from "@/hooks/useInterval";
import { BackButton, PILL_PRIMARY, SetupShell } from "./SetupShell";

export function OllamaStep({ onRunning, onBack, onKeys }: { onRunning: () => void; onBack: () => void; onKeys: () => void }) {
  // Re-check every 2 s; the screen advances by itself once Ollama answers.
  useInterval(() => {
    getSetupStatus()
      .then((s) => {
        if (s.ollama.running) onRunning();
      })
      .catch(() => {});
  }, 2000);

  return (
    <SetupShell
      back={<BackButton onClick={onBack} />}
      eyebrow="SET UP · STEP 2 OF 3"
      title="Install Ollama"
      description="Ollama is the free app that runs the models. Install it, open it once, and this screen will notice on its own."
    >
      <div className="flex flex-col gap-[22px] rounded-[22px] border border-ink-800 bg-ink-900 p-7">
        <ol className="flex list-decimal flex-col gap-2.5 pl-5 text-[15px] leading-normal text-ink-100">
          <li>Download Ollama for your computer.</li>
          <li>Open the installer and follow its steps.</li>
          <li>Open Ollama. You&apos;ll see its icon in the menu bar or system tray.</li>
        </ol>
        <div className="flex flex-wrap items-center gap-[18px]">
          <a href="https://ollama.com/download" target="_blank" rel="noreferrer" className={PILL_PRIMARY}>
            Download Ollama
            <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
          </a>
          <span role="status" className="inline-flex items-center gap-2.5 text-sm text-ink-400">
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-warning-solid" aria-hidden="true" />
            Waiting for Ollama… checking every 2 seconds
          </span>
        </div>
      </div>

      <p className="text-[13px] text-ink-500">
        Already installed? Make sure it&apos;s running.{" "}
        <button type="button" onClick={onKeys} className="text-ink-300 underline underline-offset-2 hover:text-ink-100">
          Use API keys instead
        </button>
      </p>
    </SetupShell>
  );
}
