import Link from "next/link";
import { SourceIcon } from "@/components/icons/SourceIcon";

const FEATURES = [
  {
    title: "No more doc archaeology",
    description:
      "Stop grepping through Notion spaces you don't remember the name of. Ask in plain English, get the answer.",
    accent: "coral" as const,
  },
  {
    title: "Citations, not guesses",
    description: "Every claim links to the exact page or ticket it came from — verify it yourself in one click.",
    accent: "gold" as const,
  },
  {
    title: "Live, not stale",
    description: "Sync on demand. Ask again after an update and get the current answer, not a cached one.",
    accent: "coral" as const,
  },
  {
    title: "Your data stays yours",
    description: "Every org gets an isolated knowledge base, with admin-controlled access down to the connection.",
    accent: "gold" as const,
  },
];

export function LandingPage() {
  return (
    <div className="relative overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-[-10rem] h-[36rem] w-[36rem] -translate-x-1/2 rounded-full bg-coral-500/20 blur-[120px]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute right-[-6rem] top-40 h-72 w-72 rounded-full bg-gold-400/15 blur-[100px]"
      />

      <section className="relative mx-auto grid max-w-6xl gap-14 px-6 pb-20 pt-24 lg:grid-cols-[1.1fr_1fr] lg:items-center lg:pt-28">
        <div className="text-center lg:text-left">
          <span className="mb-6 inline-flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-900/70 px-3 py-1 text-xs font-medium text-ink-300">
            <span className="h-1.5 w-1.5 rounded-full bg-coral-500" />
            For engineering & product teams
          </span>

          <h1 className="text-4xl font-semibold leading-tight tracking-tight text-ink-50 sm:text-5xl">
            Stop searching Notion.
            <br />
            <span className="bg-gradient-to-r from-coral-400 to-gold-400 bg-clip-text text-transparent">
              Start asking.
            </span>
          </h1>

          <p className="mx-auto mt-6 max-w-xl text-base text-ink-400 lg:mx-0">
            Connect Notion and Jira once. Ask questions in plain English. Every answer links straight back to the
            page or ticket it came from, so you verify it in one click instead of trusting it blind.
          </p>

          <div className="mt-10 flex flex-wrap items-center justify-center gap-3 lg:justify-start">
            <Link
              href="/signup"
              className="rounded-full bg-coral-500 px-6 py-2.5 text-sm font-semibold text-white shadow-glow transition hover:bg-coral-400"
            >
              Start free — no card required
            </Link>
            <Link
              href="/login"
              className="rounded-full border border-ink-700 bg-ink-900/60 px-6 py-2.5 text-sm font-semibold text-ink-200 transition hover:border-ink-600 hover:bg-ink-800/60"
            >
              Log in
            </Link>
          </div>
          <p className="mt-4 text-xs text-ink-600">Free for your whole team, forever. Takes about 2 minutes to connect.</p>
        </div>

        <ChatPreview />
      </section>

      <section className="relative mx-auto max-w-5xl px-6 pb-24">
        <div className="grid gap-4 sm:grid-cols-2">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="rounded-2xl border border-ink-800 bg-ink-900/60 p-6 shadow-panel transition hover:border-ink-700"
            >
              <span
                className={`mb-4 inline-flex h-9 w-9 items-center justify-center rounded-lg ${
                  f.accent === "coral" ? "bg-coral-500/15 text-coral-400" : "bg-gold-400/15 text-gold-400"
                }`}
              >
                <span className="h-2.5 w-2.5 rounded-full bg-current" />
              </span>
              <h3 className="mb-1.5 text-sm font-semibold text-ink-100">{f.title}</h3>
              <p className="text-sm leading-relaxed text-ink-400">{f.description}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="relative mx-auto max-w-3xl px-6 pb-24 text-center">
        <div className="rounded-2xl border border-ink-800 bg-gradient-to-br from-ink-900 to-ink-900/40 p-8 shadow-panel">
          <h2 className="mb-2 text-lg font-semibold text-ink-100">Ready to stop searching?</h2>
          <p className="mb-6 text-sm text-ink-400">
            Create your organization, connect Notion or Jira, and ask your first question in under 2 minutes. Free —
            no card, no trial clock, ever.
          </p>
          <Link
            href="/signup"
            className="inline-block rounded-full bg-coral-500 px-6 py-2.5 text-sm font-semibold text-white shadow-glow transition hover:bg-coral-400"
          >
            Create your organization
          </Link>
        </div>
      </section>
    </div>
  );
}

/**
 * Static mock of the actual chat + citation UI — the product's real
 * differentiator is "answers you can verify," which is a visual claim, not a
 * verbal one. Show it instead of describing it. Deliberately hand-rolled
 * rather than reusing components/chat/* — those expect live streaming state,
 * a static hero mock has no business depending on that.
 */
function ChatPreview() {
  return (
    <div className="relative mx-auto w-full max-w-md rounded-2xl border border-ink-800 bg-ink-900/80 shadow-panel">
      <div className="flex items-center gap-1.5 border-b border-ink-800 px-4 py-3">
        <span className="h-2.5 w-2.5 rounded-full bg-ink-700" />
        <span className="h-2.5 w-2.5 rounded-full bg-ink-700" />
        <span className="h-2.5 w-2.5 rounded-full bg-ink-700" />
        <span className="ml-2 text-xs text-ink-500">Chat</span>
      </div>

      <div className="space-y-3 p-4">
        <div className="ml-auto max-w-[85%] rounded-xl rounded-tr-sm bg-ink-800 px-3 py-2 text-xs text-ink-200">
          What's our current plan for the SSO rollout?
        </div>

        <div className="max-w-[92%] rounded-xl rounded-tl-sm border border-ink-800 bg-ink-950/60 px-3 py-2.5 text-xs leading-relaxed text-ink-300">
          Rollout is staged in three phases, starting with internal accounts{" "}
          <Cite n={1} /> and expanding to enterprise customers once backend SAML support ships{" "}
          <Cite n={2} />. Target for phase one is end of next sprint.
        </div>
      </div>

      <div className="space-y-2 border-t border-ink-800 p-4">
        <p className="text-[11px] font-medium uppercase tracking-wide text-ink-500">Cited in answer</p>
        <SourceRow index={1} source="notion" title="SSO Rollout Plan — Q3" />
        <SourceRow index={2} source="jira" title="ENG-482: SAML backend support" />
      </div>
    </div>
  );
}

function Cite({ n }: { n: number }) {
  return (
    <span className="mx-0.5 inline-flex h-4 w-4 items-center justify-center rounded bg-gold-400/25 text-[10px] font-semibold text-gold-300">
      {n}
    </span>
  );
}

function SourceRow({ index, source, title }: { index: number; source: "notion" | "jira"; title: string }) {
  return (
    <div className="flex items-center gap-1.5 rounded-xl border border-gold-400/30 bg-gold-400/5 p-2 text-xs">
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-gold-400/25 text-[10px] font-semibold text-gold-300">
        {index}
      </span>
      <SourceIcon source={source} className="h-3.5 w-3.5 shrink-0 text-ink-400" />
      <span className="truncate font-medium text-ink-200">{title}</span>
    </div>
  );
}
