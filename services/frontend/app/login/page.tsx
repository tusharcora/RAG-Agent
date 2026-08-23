"use client";

import { useState } from "react";
import Link from "next/link";
import { login } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

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
    } catch {
      setError("Invalid email or password.");
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
          <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" />
          <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" />
          {error && <p className="text-sm text-coral-400">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-xl bg-coral-500 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-coral-400 disabled:opacity-50"
          >
            {submitting ? "Logging in…" : "Log in"}
          </button>
        </form>

        {/* Social login slot — Google/GitHub OAuth buttons render here. */}
        <div className="my-5 flex items-center gap-3 text-xs text-ink-600">
          <span className="h-px flex-1 bg-ink-800" />
          or continue with
          <span className="h-px flex-1 bg-ink-800" />
        </div>
        <div id="social-login-slot" className="flex flex-col gap-2" />

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

function Field({
  label,
  type,
  value,
  onChange,
  autoComplete,
}: {
  label: string;
  type: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm text-ink-400">{label}</span>
      <input
        type={type}
        required
        value={value}
        autoComplete={autoComplete}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100 outline-none focus:border-coral-500"
      />
    </label>
  );
}
