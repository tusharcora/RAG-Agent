"use client";

import { useState } from "react";
import { useConnectFlow } from "@/hooks/useConnectFlow";
import { markPromptSeen, type ConnectPurpose } from "@/lib/connect";
import { ProviderButton } from "@/components/ProviderButton";
import { SetupShell } from "./SetupShell";

/** First screen of first-run setup: optional Google/GitHub sign-in. */
export function AccountStep({ onDone }: { onDone: () => void }) {
  const [finishing, setFinishing] = useState(false);

  async function proceed() {
    setFinishing(true);
    await markPromptSeen().catch(() => {});
    onDone();
  }

  const { flow, start, cancel } = useConnectFlow({ onDone: () => void proceed() });
  const waiting = flow.phase === "waiting";

  return (
    <SetupShell
      eyebrow="WELCOME TO KNOWLEDGE AGENT"
      title={
        <>
          Make it <span className="hl italic px-1.5">yours</span>.
        </>
      }
      description="Sign in so your workspace has your name on it, and so you can share it with teammates later. It's optional: your documents and chats stay on this computer either way."
    >
      {waiting ? (
        <div className="flex flex-col items-start gap-4" role="status">
          <p className="text-[15px] text-ink-100">Finish signing in in your browser…</p>
          <button type="button" onClick={cancel} className="text-sm text-ink-400 underline-offset-2 transition hover:text-ink-100 hover:underline">
            Cancel
          </button>
        </div>
      ) : (
        <div className="flex flex-col items-start gap-5">
          <div className="flex flex-wrap gap-3">
            {(["google", "github"] as const).map((p) => (
              <ProviderButton key={p} provider={p} isDisabled={finishing} onPress={() => start(p as ConnectPurpose)}>
                Continue with {p === "google" ? "Google" : "GitHub"}
              </ProviderButton>
            ))}
          </div>
          {flow.phase === "error" && <p className="text-sm text-error-primary">{flow.message}</p>}
          <button
            type="button"
            onClick={() => void proceed()}
            disabled={finishing}
            className="text-sm text-ink-400 underline-offset-2 transition hover:text-ink-100 hover:underline disabled:opacity-50"
          >
            Skip for now
          </button>
          <p className="text-[13px] text-ink-500">Opens in your browser. We only receive your name, email and picture.</p>
        </div>
      )}
    </SetupShell>
  );
}
