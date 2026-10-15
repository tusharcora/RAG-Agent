"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { signOut, unlink } from "@/lib/connect";
import { useConnectFlow } from "@/hooks/useConnectFlow";
import { ProviderButton } from "@/components/ProviderButton";
import { Button } from "@/components/base/buttons/button";
import { toast } from "@/lib/toast";

type Provider = "google" | "github";

const PROVIDERS: { id: Provider; label: string }[] = [
  { id: "google", label: "Google" },
  { id: "github", label: "GitHub" },
];

/** Desktop only: optional Google/GitHub sign-in for the local profile. */
export function AccountSection() {
  const { account, refresh } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const [unlinking, setUnlinking] = useState<Provider | null>(null);
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

  async function handleUnlink(provider: Provider, label: string) {
    setUnlinking(provider);
    try {
      await unlink(provider);
      await refresh();
    } catch (err) {
      toast.error(`Couldn't unlink ${label}`, (err as { detail?: string })?.detail ?? "Please try again.");
    } finally {
      setUnlinking(null);
    }
  }

  const name = account.name || account.email || "You";
  const linked = new Map((account.identities ?? []).map((i) => [i.provider, i]));

  const waiting =
    flow.phase === "waiting" ? (
      <div className="flex flex-wrap items-center gap-4" role="status">
        <p className="text-sm text-ink-100">Finish signing in in your browser…</p>
        <Button color="tertiary" size="sm" onPress={cancel}>
          Cancel
        </Button>
        <a href={flow.url} target="_blank" rel="noopener" className="text-sm font-semibold text-ink-400 underline-offset-2 hover:text-ink-100 hover:underline">
          Reopen the sign-in page
        </a>
      </div>
    ) : null;
  const error = flow.phase === "error" ? <p className="text-sm text-error-primary">{flow.message}</p> : null;

  return (
    <section aria-labelledby="desktop-account" className="flex flex-col gap-4">
      <h2 id="desktop-account" className="text-[15px] font-semibold text-ink-100">
        Account
      </h2>
      <div className="flex flex-col gap-5 rounded-2xl border border-ink-800 bg-ink-900 p-6">
        {account.signed_in ? (
          <>
            <div className="flex flex-wrap items-center gap-4">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-ink-100 text-base font-semibold text-ink-950" aria-hidden="true">
                {name.slice(0, 1).toUpperCase()}
              </span>
              <div className="min-w-0 flex-1 leading-tight">
                <p className="truncate text-[15px] font-medium text-ink-100">{name}</p>
                {account.email && <p className="truncate text-sm text-ink-500">{account.email}</p>}
              </div>
            </div>

            <ul aria-label="Linked accounts" className="flex flex-col divide-y divide-ink-800 border-y border-ink-800">
              {PROVIDERS.map(({ id, label }) => {
                const identity = linked.get(id);
                return (
                  <li key={id} className="flex flex-wrap items-center gap-3 py-3">
                    <div className="min-w-0 flex-1 leading-tight">
                      <p className="text-sm font-medium text-ink-100">{label}</p>
                      <p className="truncate text-[13px] text-ink-500">
                        {identity ? `Linked${identity.email ? ` · ${identity.email}` : ""}` : "Not linked"}
                      </p>
                    </div>
                    {identity ? (
                      <Button
                        color="secondary"
                        size="sm"
                        aria-label={`Unlink ${label}`}
                        isLoading={unlinking === id}
                        isDisabled={unlinking !== null}
                        onPress={() => handleUnlink(id, label)}
                      >
                        Unlink
                      </Button>
                    ) : (
                      <Button
                        color="secondary"
                        size="sm"
                        aria-label={`Link ${label}`}
                        isDisabled={starting || flow.phase === "waiting"}
                        onPress={() => start(id)}
                      >
                        Link
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
            {waiting}
            {error}

            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="min-w-0 flex-1 text-[13px] leading-relaxed text-ink-500">
                Signing out keeps your linked accounts; sign in again with either one to come back.
              </p>
              <Button color="secondary" size="sm" isLoading={signingOut} onPress={handleSignOut}>
                Sign out
              </Button>
            </div>
          </>
        ) : (
          waiting ?? (
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
              {error}
            </div>
          )
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
