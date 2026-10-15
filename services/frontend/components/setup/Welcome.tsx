"use client";

import { SetupShell } from "./SetupShell";

export function Welcome({ onLocal, onKeys }: { onLocal: () => void; onKeys: () => void }) {
  return (
    <SetupShell
      eyebrow="SET UP"
      title={
        <>
          How should answers be <span className="hl italic px-1.5">made</span>?
        </>
      }
      description="Your documents and chats stay on this computer either way. This only decides where the language models run."
    >
      <div className="grid gap-5 md:grid-cols-2">
        <button
          type="button"
          onClick={onLocal}
          className="flex min-h-[260px] flex-col gap-3.5 rounded-[22px] border-2 border-ink-100 bg-ink-900 p-7 text-left transition hover:bg-ink-850"
        >
          <span className="flex items-center justify-between gap-3">
            <span className="text-xl font-semibold text-ink-100">Run models on this computer</span>
            <span className="shrink-0 rounded-full bg-accent px-2.5 py-1 font-mono text-[11px] tracking-[0.06em] text-accent-ink">
              RECOMMENDED
            </span>
          </span>
          <span className="text-[15px] leading-relaxed text-ink-400">
            Free and fully private. Uses Ollama, a free app that runs open models. About 2.3 GB to download once.
          </span>
          <span className="mt-auto flex flex-col gap-1.5 text-[13px] text-ink-400">
            <span>✓ Works offline after setup</span>
            <span>✓ No accounts or keys</span>
            <span>· Slower on older computers</span>
          </span>
        </button>

        <button
          type="button"
          onClick={onKeys}
          className="flex min-h-[260px] flex-col gap-3.5 rounded-[22px] border border-ink-800 bg-ink-900 p-7 text-left transition hover:border-ink-700 hover:bg-ink-850"
        >
          <span className="text-xl font-semibold text-ink-100">Use API keys instead</span>
          <span className="text-[15px] leading-relaxed text-ink-400">
            Faster, stronger answers from Claude, OpenAI or Gemini, plus Voyage for search. You pay those providers for what you use.
          </span>
          <span className="mt-auto flex flex-col gap-1.5 text-[13px] text-ink-400">
            <span>✓ Nothing to download</span>
            <span>✓ Best answer quality</span>
            <span>· Snippets of your documents are sent to the provider</span>
          </span>
        </button>
      </div>
    </SetupShell>
  );
}
