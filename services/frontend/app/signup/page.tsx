"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { signup } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function SignupPage() {
  return (
    <Suspense>
      <SignupForm />
    </Suspense>
  );
}

function SignupForm() {
  const searchParams = useSearchParams();
  const inviteToken = searchParams.get("invite") ?? "";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { refresh } = useAuth();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await signup({
        email,
        password,
        display_name: displayName || undefined,
        org_name: inviteToken ? undefined : orgName,
        invite_token: inviteToken || undefined,
      });
      await refresh();
    } catch {
      setError("Could not create account — the email may already be registered.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-[calc(100vh-57px)] items-center justify-center px-4 py-12">
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/4 h-96 w-96 -translate-x-1/2 rounded-full bg-gold-400/10 blur-[100px]"
      />
      <div className="relative w-full max-w-sm rounded-2xl border border-ink-800 bg-ink-900/60 p-7 shadow-panel">
        <h1 className="mb-1 text-lg font-semibold text-ink-50">
          {inviteToken ? "Accept invite" : "Create your organization"}
        </h1>
        <p className="mb-6 text-sm text-ink-500">
          {inviteToken ? "You've been invited to join an organization." : "Start with a free workspace for your team."}
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" />
          <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="new-password" />
          <Field label="Display name (optional)" type="text" value={displayName} onChange={setDisplayName} required={false} />
          {!inviteToken && <Field label="Organization name" type="text" value={orgName} onChange={setOrgName} />}
          {error && <p className="text-sm text-coral-400">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-xl bg-coral-500 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-coral-400 disabled:opacity-50"
          >
            {submitting ? "Creating account…" : "Sign up"}
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
          Already have an account?{" "}
          <Link href="/login" className="text-coral-400 hover:text-coral-300">
            Log in
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
  required = true,
}: {
  label: string;
  type: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm text-ink-400">{label}</span>
      <input
        type={type}
        required={required}
        value={value}
        autoComplete={autoComplete}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100 outline-none focus:border-coral-500"
      />
    </label>
  );
}
