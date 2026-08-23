"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { me as fetchMe, logout as apiLogout } from "./api";
import type { MeResponse } from "./types";

interface AuthState {
  user: MeResponse | null;
  loading: boolean;
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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();

  async function refresh() {
    try {
      const current = await fetchMe();
      setUser(current);
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
    const isPublic = PUBLIC_PATHS.has(pathname);
    if (!user && !isPublic) router.replace("/login");
    if (user && AUTH_ONLY_PATHS.has(pathname)) router.replace("/");
  }, [loading, user, pathname, router]);

  async function logout() {
    await apiLogout();
    setUser(null);
    router.replace("/login");
  }

  return <AuthContext.Provider value={{ user, loading, refresh, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
