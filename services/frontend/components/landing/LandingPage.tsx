import Link from "next/link";

const FEATURES = [
  {
    title: "Connect Notion & Jira",
    description: "OAuth into your workspace and Jira site — no manual exports, no copy-pasting docs.",
    accent: "coral" as const,
  },
  {
    title: "Ask in plain English",
    description: "Every question is answered from what's actually been synced, streamed back token by token.",
    accent: "gold" as const,
  },
  {
    title: "Every answer cited",
    description: "See exactly which pages and issues backed each answer — cited vs. searched-but-unused, side by side.",
    accent: "coral" as const,
  },
  {
    title: "Built for your whole team",
    description: "Each organization gets an isolated knowledge base, with admin-controlled access per connection.",
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

      <section className="relative mx-auto max-w-4xl px-6 pb-20 pt-24 text-center">
        <span className="mb-6 inline-flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-900/70 px-3 py-1 text-xs font-medium text-ink-300">
          <span className="h-1.5 w-1.5 rounded-full bg-coral-500" />
          Grounded answers, real citations
        </span>

        <h1 className="text-4xl font-semibold leading-tight tracking-tight text-ink-50 sm:text-5xl">
          Ask your Notion and Jira anything.
          <br />
          <span className="bg-gradient-to-r from-coral-400 to-gold-400 bg-clip-text text-transparent">
            Get answers you can verify.
          </span>
        </h1>

        <p className="mx-auto mt-6 max-w-xl text-base text-ink-400">
          Connect your workspace, sync your content, and get grounded answers with inline citations back to the
          exact page or issue they came from — nothing invented, nothing untraceable.
        </p>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/signup"
            className="rounded-full bg-coral-500 px-6 py-2.5 text-sm font-semibold text-white shadow-glow transition hover:bg-coral-400"
          >
            Get started free
          </Link>
          <Link
            href="/login"
            className="rounded-full border border-ink-700 bg-ink-900/60 px-6 py-2.5 text-sm font-semibold text-ink-200 transition hover:border-ink-600 hover:bg-ink-800/60"
          >
            Log in
          </Link>
        </div>
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
          <h2 className="mb-2 text-lg font-semibold text-ink-100">Ready to try it on your own workspace?</h2>
          <p className="mb-6 text-sm text-ink-400">Create an account, connect Notion or Jira, and ask your first question in minutes.</p>
          <Link
            href="/signup"
            className="inline-block rounded-full bg-coral-500 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-coral-400"
          >
            Create your organization
          </Link>
        </div>
      </section>
    </div>
  );
}
