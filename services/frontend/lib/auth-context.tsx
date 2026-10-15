"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { me as fetchMe, logout as apiLogout, localSession } from "./api";
import { getSetupStatus } from "./setup";
import { getAccount, type Account } from "./connect";
import type { MeResponse } from "./types";

interface AuthState {
  user: MeResponse | null;
  loading: boolean;
  /** Desktop only: whether first-run setup is finished. null = unknown / not desktop. */
  setupComplete: boolean | null;
  /** Desktop only: optional Google/GitHub sign-in state. null = unknown / not desktop. */
  account: Account | null;
  refreshAccount: () => Promise<void>;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

// "/" is public too (it renders a marketing landing page for logged-out
// visitors — see app/page.tsx — instead of redirecting them away), but unlike
// /login and /signup it must NOT redirect an already-logged-in visitor
// elsewhere: "/" is also the chat dashboard once authenticated, so a logged-in
// user staying there is correct, not something to bounce away from.
const PUBLIC_PATHS = new Set(["/", "/login", "/signup"]);
const AUTH_ONLY_PATHS = new Set(["/login", "/signup"]);

// The desktop app is single-user: there's no sign-in screen, the API hands
// this computer's one local account a session on request.
export const IS_DESKTOP = process.env.NEXT_PUBLIC_DESKTOP === "1";

// The desktop build exports with trailing slashes ("/login/").
const normalize = (p: string) => (p.length > 1 ? p.replace(/\/+$/, "") : p);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [setupComplete, setSetupComplete] = useState<boolean | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const pathname = usePathname();
  const router = useRouter();

  async function refreshAccount() {
    if (!IS_DESKTOP) return;
    try {
      setAccount(await getAccount());
    } catch {
      setAccount(null);
    }
  }

  async function refresh() {
    try {
      const current = IS_DESKTOP ? await localSession() : await fetchMe();
      setUser(current);
      if (IS_DESKTOP) {
        try {
          setSetupComplete((await getSetupStatus()).setup_complete);
        } catch {
          setSetupComplete(null); // unknown: don't trap the user on /setup
        }
        await refreshAccount();
      }
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (loading) return;
    const path = normalize(pathname);
    if (IS_DESKTOP) {
      if (AUTH_ONLY_PATHS.has(path)) router.replace("/");
      else if (setupComplete === false && path !== "/setup") router.replace("/setup");
      else if (setupComplete === true && path === "/setup") router.replace("/");
      return;
    }
    const isPublic = PUBLIC_PATHS.has(path);
    if (!user && !isPublic) router.replace("/login");
    if (user && AUTH_ONLY_PATHS.has(path)) router.replace("/");
  }, [loading, user, setupComplete, pathname, router]);

  async function logout() {
    if (IS_DESKTOP) return; // nothing to sign out of on a single-user install
    await apiLogout();
    setUser(null);
    router.replace("/login");
  }

  return <AuthContext.Provider value={{ user, loading, setupComplete, account, refreshAccount, refresh, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
