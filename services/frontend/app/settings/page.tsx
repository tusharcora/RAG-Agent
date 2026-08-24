"use client";

import { useAuth } from "@/lib/auth-context";
import { ThemeToggle } from "@/components/ThemeToggle";

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-ink-800 py-3 last:border-b-0">
      <span className="text-sm text-ink-500">{label}</span>
      <span className="text-sm font-medium text-ink-100">{value}</span>
    </div>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();

  if (!user) return null;

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <div className="mb-6">
        <h1 className="mb-1 text-xl font-semibold text-ink-100">Settings</h1>
        <p className="text-sm text-ink-500">Your account and preferences.</p>
      </div>

      <section className="cyber-chamfer mb-6 rounded-2xl border border-ink-800 bg-ink-900/60 p-5 shadow-panel">
        <h2 className="mb-2 text-sm font-semibold text-ink-100">Account</h2>
        <InfoRow label="Name" value={user.display_name || "—"} />
        <InfoRow label="Email" value={user.email} />
        <InfoRow label="Role" value={user.role} />
        <InfoRow label="Organization" value={user.org_name} />
      </section>

      <section className="cyber-chamfer mb-6 rounded-2xl border border-ink-800 bg-ink-900/60 p-5 shadow-panel">
        <h2 className="mb-3 text-sm font-semibold text-ink-100">Appearance</h2>
        <p className="mb-3 text-sm text-ink-500">Choose how the app looks.</p>
        <ThemeToggle />
      </section>

      <section className="rounded-2xl border border-dashed border-ink-700 p-6 text-center">
        <p className="text-sm text-ink-500">More settings coming soon.</p>
      </section>
    </div>
  );
}
