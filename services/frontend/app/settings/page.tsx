"use client";

import { useAuth } from "@/lib/auth-context";
import { ACCENTS, useTheme, type ThemeMode } from "@/lib/theme-context";
import { ModelSettings } from "@/components/settings/ModelSettings";
import { McpSettings } from "@/components/settings/McpSettings";
import { MembersSection } from "@/components/settings/MembersSection";
import { InviteSection } from "@/components/settings/InviteSection";
import { ServiceTokensSection } from "@/components/settings/ServiceTokensSection";

const MODES: { id: ThemeMode; label: string; thumb: string; bars: [string, string, string] }[] = [
  {
    id: "system",
    label: "System",
    thumb: "linear-gradient(115deg,#F5F5F2 0 50%,#262624 50% 100%)",
    bars: ["#B9B8B1", "#8F8E88", "#C9C8C1"],
  },
  { id: "light", label: "Light", thumb: "#F5F5F2", bars: ["#111113", "#B9B8B1", "#D4D3CC"] },
  { id: "dark", label: "Dark", thumb: "#262624", bars: ["#FAF9F5", "#6B6A64", "#4D4D49"] },
];

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
  const { mode, setMode, accent, setAccent } = useTheme();

  if (!user) return null;
  const canEdit = user.role === "owner" || user.role === "admin";

  return (
    <div className="mx-auto flex max-w-[880px] flex-col gap-10 px-6 py-10 md:px-12">
      <header className="flex flex-col gap-2">
        <span className="font-mono text-[11px] tracking-[0.08em] text-ink-500">SETTINGS</span>
        <h1 className="font-serif text-5xl tracking-tight text-ink-100">Make it yours.</h1>
      </header>

      <section aria-labelledby="appearance" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="appearance" className="text-[15px] font-semibold text-ink-100">
            Appearance
          </h2>
          <span className="text-[13px] text-ink-500">System follows your device, day and night</span>
        </div>
        <div role="radiogroup" aria-label="Appearance" className="flex flex-wrap gap-4">
          {MODES.map((m) => {
            const on = mode === m.id;
            return (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={m.label}
                onClick={() => setMode(m.id)}
                className={`flex min-w-[10rem] max-w-[16rem] flex-1 basis-40 flex-col gap-3 rounded-2xl border bg-ink-900 p-3 text-left transition ${
                  on ? "border-ink-100 ring-1 ring-ink-100" : "border-ink-800 hover:border-ink-700"
                }`}
              >
                <span
                  className="flex h-[92px] flex-col justify-center gap-[7px] rounded-[10px] border border-ink-800 px-[18px]"
                  style={{ background: m.thumb }}
                  aria-hidden="true"
                >
                  <span className="h-2 w-[70%] rounded" style={{ background: m.bars[0] }} />
                  <span className="h-1.5 w-[90%] rounded" style={{ background: m.bars[1] }} />
                  <span className="h-1.5 w-[55%] rounded" style={{ background: m.bars[2] }} />
                </span>
                <span className="flex w-full items-center justify-between">
                  <span className="text-sm font-medium text-ink-100">{m.label}</span>
                  <span
                    className={`h-4 w-4 rounded-full border ${on ? "border-ink-100 bg-ink-100" : "border-ink-700"}`}
                    aria-hidden="true"
                  />
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section aria-labelledby="accent" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="accent" className="text-[15px] font-semibold text-ink-100">
            Highlighter colour
          </h2>
          <span className="text-[13px] text-ink-500">Used for cited phrases and citation chips</span>
        </div>
        <div role="radiogroup" aria-label="Highlighter colour" className="flex flex-wrap gap-3.5">
          {ACCENTS.map((a) => {
            const on = accent === a.id;
            return (
              <button
                key={a.id}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={a.label}
                onClick={() => setAccent(a.id)}
                className={`flex w-[104px] flex-col items-center gap-2 rounded-2xl border bg-ink-900 px-2 pt-3 pb-2.5 transition ${
                  on ? "border-ink-100" : "border-ink-800 hover:border-ink-700"
                }`}
              >
                <span
                  className="flex h-11 w-11 items-center justify-center rounded-full text-lg font-semibold text-[#111113]"
                  style={{ background: `rgb(${a.rgb})` }}
                >
                  {on ? "✓" : ""}
                </span>
                <span className="text-[12.5px] text-ink-100">{a.label}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section aria-labelledby="preview" className="flex flex-col gap-4">
        <h2 id="preview" className="text-[15px] font-semibold text-ink-100">
          Preview
        </h2>
        <div className="flex flex-col gap-5 rounded-2xl border border-ink-800 bg-ink-900 p-7">
          <div className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:items-baseline">
            <span className="font-mono text-[11px] tracking-[0.06em] text-success-primary">● SUPPORTED</span>
            <p className="font-serif text-[27px] leading-[1.38] text-ink-100">
              <span className="hl">The Q4 infrastructure freeze starts on November 1, per the Release Runbook.</span>
              <span className="ml-1.5 inline-flex h-[22px] min-w-[22px] -translate-y-1.5 items-center justify-center rounded-full bg-accent px-1.5 font-mono text-[11px] font-medium text-accent-ink">
                1
              </span>
            </p>
          </div>
          <div className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:items-baseline">
            <span className="font-mono text-[11px] tracking-[0.06em] text-warning-primary">◐ PARTIAL</span>
            <p className="font-serif text-[27px] leading-[1.38] text-ink-100">
              <span className="underline decoration-warning-primary decoration-2 decoration-dotted underline-offset-[7px]">
                Rollback has been rehearsed in staging.
              </span>
            </p>
          </div>
        </div>
      </section>

      <ModelSettings canEdit={canEdit} />

      <McpSettings canEdit={canEdit} />

      <MembersSection />

      {canEdit && (
        <>
          <InviteSection />
          <ServiceTokensSection />
        </>
      )}

      <section aria-labelledby="account" className="rounded-2xl border border-ink-800 bg-ink-900 p-5">
        <h2 id="account" className="mb-2 text-sm font-semibold text-ink-100">
          Account
        </h2>
        <InfoRow label="Name" value={user.display_name || "—"} />
        <InfoRow label="Email" value={user.email} />
        <InfoRow label="Role" value={user.role} />
        <InfoRow label="Organization" value={user.org_name} />
      </section>
    </div>
  );
}
