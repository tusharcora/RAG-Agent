"use client";

import { useState } from "react";
import Link from "next/link";
import { ApiError, login } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { SocialLoginButtons } from "@/components/auth/SocialLoginButtons";
import { Input } from "@/components/base/input/input";
import { Button } from "@/components/base/buttons/button";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { refresh } = useAuth();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
      await refresh();
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 429)) {
        setError(err.status === 429 ? "Too many attempts — try again in a minute." : "Invalid email or password.");
      } else {
        setError("Couldn't reach the server. Check your connection and try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-[calc(100vh-57px)] items-center justify-center px-4">
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/4 h-96 w-96 -translate-x-1/2 rounded-full bg-coral-500/10 blur-[100px]"
      />
      <div className="relative w-full max-w-sm rounded-2xl border border-ink-800 bg-ink-900/60 p-7 shadow-panel">
        <h1 className="mb-1 text-lg font-semibold text-ink-50">Welcome back</h1>
        <p className="mb-6 text-sm text-ink-500">Log in to your organization's knowledge base.</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input label="Email" type="email" value={email} onChange={setEmail} isRequired autoComplete="email" />
          <Input
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            isRequired
            autoComplete="current-password"
          />
          {error && <p className="text-sm text-coral-400">{error}</p>}
          <Button type="submit" isDisabled={submitting} isLoading={submitting} className="w-full" size="lg">
            Log in
          </Button>
        </form>

        {/* Social login slot — Google/GitHub OAuth buttons render here. */}
        <div className="my-5 flex items-center gap-3 text-xs text-ink-600">
          <span className="h-px flex-1 bg-ink-800" />
          or continue with
          <span className="h-px flex-1 bg-ink-800" />
        </div>
        <SocialLoginButtons />

        <p className="mt-6 text-center text-sm text-ink-500">
          No account?{" "}
          <Link href="/signup" className="text-coral-400 hover:text-coral-300">
            Sign up
          </Link>
        </p>
      </div>
    </div>
  );
}
