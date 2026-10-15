"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { signOut } from "@/lib/connect";
import { useConnectFlow } from "@/hooks/useConnectFlow";
import { ProviderButton } from "@/components/ProviderButton";
import { Button } from "@/components/base/buttons/button";
import { toast } from "@/lib/toast";

const PROVIDER_LABEL: Record<string, string> = { google: "Google", github: "GitHub" };

/** Desktop only: optional Google/GitHub sign-in for the local profile. */
export function AccountSection() {
  const { account, refresh } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const { flow, start, cancel, starting } = useConnectFlow({ onSettled: () => void refresh() });

  if (!account?.broker_configured) return null;

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await signOut();
      await refresh();
    } catch (err) {
      toast.error("Couldn't sign out", (err as { detail?: string })?.detail ?? "Please try again.");
    } finally {
      setSigningOut(false);
    }
  }

  const name = account.name || account.email || "You";

  return (
    <section aria-labelledby="desktop-account" className="flex flex-col gap-4">
      <h2 id="desktop-account" className="text-[15px] font-semibold text-ink-100">
        Account
      </h2>
      <div className="flex flex-col gap-5 rounded-2xl border border-ink-800 bg-ink-900 p-6">
        {account.signed_in ? (
          <div className="flex flex-wrap items-center gap-4">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-ink-100 text-base font-semibold text-ink-950" aria-hidden="true">
              {name.slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1 leading-tight">
              <p className="truncate text-[15px] font-medium text-ink-100">{account.name || name}</p>
              <p className="truncate text-sm text-ink-500">
                {account.email ? `${account.email} · ` : ""}signed in with {PROVIDER_LABEL[account.provider ?? ""] ?? account.provider}
              </p>
            </div>
            <Button color="secondary" size="sm" isLoading={signingOut} onPress={handleSignOut}>
              Sign out
            </Button>
          </div>
        ) : flow.phase === "waiting" ? (
          <div className="flex flex-wrap items-center gap-4" role="status">
            <p className="text-sm text-ink-100">Finish signing in in your browser…</p>
            <Button color="tertiary" size="sm" onPress={cancel}>
              Cancel
            </Button>
            <a href={flow.url} target="_blank" rel="noopener" className="text-sm font-semibold text-ink-400 underline-offset-2 hover:text-ink-100 hover:underline">
              Reopen the sign-in page
            </a>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-3">
            <p className="text-sm text-ink-400">Sign in so your workspace has your name on it. It&apos;s optional.</p>
            <div className="flex flex-wrap gap-3">
              <ProviderButton provider="google" isDisabled={starting} onPress={() => start("google")}>
                Continue with Google
              </ProviderButton>
              <ProviderButton provider="github" isDisabled={starting} onPress={() => start("github")}>
                Continue with GitHub
              </ProviderButton>
            </div>
            {flow.phase === "error" && <p className="text-sm text-error-primary">{flow.message}</p>}
          </div>
        )}

        <div className="border-t border-ink-800 pt-4">
          <p className="text-sm font-medium text-ink-100">Sharing with teammates</p>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-500">
            Coming later. Your account is what teammates will invite once shared workspaces arrive. Until then, everything stays on this computer.
          </p>
        </div>
        <p className="text-[13px] leading-relaxed text-ink-500">
          Signing out keeps all your documents, chats and connections. The app just goes back to an unnamed local profile.
        </p>
      </div>
    </section>
  );
}
