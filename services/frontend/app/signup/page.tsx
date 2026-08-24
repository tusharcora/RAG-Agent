"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ApiError, signup } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { SocialLoginButtons } from "@/components/auth/SocialLoginButtons";
import { Input } from "@/components/base/input/input";
import { Button } from "@/components/base/buttons/button";

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
    } catch (err) {
      // Distinguish an actual duplicate-email rejection (409) from every
      // other failure — a blanket guess here is actively misleading when the
      // real cause is a down/unreachable API, a bad invite token, etc.
      if (err instanceof ApiError && err.status === 409) {
        setError("That email is already registered — try logging in instead.");
      } else if (err instanceof ApiError && err.status === 400) {
        setError("That invite link looks invalid or expired.");
      } else {
        setError("Couldn't reach the server. Check your connection and try again.");
      }
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
          <Input label="Email" type="email" value={email} onChange={setEmail} isRequired autoComplete="email" />
          <Input
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            isRequired
            autoComplete="new-password"
          />
          <Input label="Display name (optional)" type="text" value={displayName} onChange={setDisplayName} />
          {!inviteToken && (
            <Input label="Organization name" type="text" value={orgName} onChange={setOrgName} isRequired />
          )}
          {error && <p className="text-sm text-coral-400">{error}</p>}
          <Button type="submit" isDisabled={submitting} isLoading={submitting} className="w-full" size="lg">
            Sign up
          </Button>
        </form>

        {/* Social login slot — Google/GitHub OAuth buttons render here. */}
        <div className="my-5 flex items-center gap-3 text-xs text-ink-600">
          <span className="h-px flex-1 bg-ink-800" />
          or continue with
          <span className="h-px flex-1 bg-ink-800" />
        </div>
        <SocialLoginButtons inviteToken={inviteToken || undefined} />

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
