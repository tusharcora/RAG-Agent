"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getConnections, getDocuments } from "@/lib/api";
import { greetingWord, providerLabel, timeAgo } from "@/lib/format";
import type { ConnectionStatus, DocumentSummary } from "@/lib/types";
import { ChatInput } from "@/components/chat/ChatInput";
import { SourceIcon } from "@/components/icons/SourceIcon";

const FALLBACK_QUESTIONS = [
  "What did we ship in the last sprint?",
  "Summarize our current product roadmap",
  "Are there any open blockers right now?",
];

interface Insight {
  question: string;
  meta: string;
  dot: "ink" | "accent";
  featured?: boolean;
  href?: string;
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

function questionFor(doc: DocumentSummary): string {
  return doc.source === "jira" ? `What's the latest on ${doc.title}?` : `What changed in “${doc.title}” recently?`;
}

export function HomeView({
  firstName,
  onSend,
  disabled,
  error,
}: {
  firstName?: string;
  onSend: (question: string) => void;
  disabled: boolean;
  error?: string | null;
}) {
  const [connections, setConnections] = useState<ConnectionStatus[]>([]);
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [docTotals, setDocTotals] = useState<Record<string, number>>({});
  const [loadedAt, setLoadedAt] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getConnections()
      .then(async (conns) => {
        if (cancelled) return;
        setConnections(conns);
        const connected = conns.filter((c) => c.connected);
        const totals = await Promise.all(
          connected.map((c) =>
            getDocuments({ source: c.provider, limit: 1 })
              .then((r) => [c.provider, r.total] as const)
              .catch(() => [c.provider, 0] as const),
          ),
        );
        if (!cancelled) setDocTotals(Object.fromEntries(totals));
      })
      .catch(() => {});
    getDocuments({ limit: 100 })
      .then((r) => {
        if (cancelled) return;
        setDocs(r.items);
        setLoadedAt(new Date().toISOString());
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const connected = connections.filter((c) => c.connected);

  const insights = useMemo<Insight[]>(() => {
    const live = docs.filter((d) => !d.excluded_from_retrieval);
    const edited = (d: DocumentSummary) => new Date(d.last_edited_at ?? d.synced_at).getTime();
    const recent = [...live].sort((a, b) => edited(b) - edited(a));
    if (recent.length === 0) {
      return [
        ...FALLBACK_QUESTIONS.map((q) => ({ question: q, meta: "Suggested question", dot: "ink" as const })),
        {
          question: "Connect Notion or Jira to see what changed this week",
          meta: "Connections · takes about 2 minutes",
          dot: "accent" as const,
          featured: true,
          href: "/connections",
        },
      ];
    }
    const cards: Insight[] = recent.slice(0, 3).map((d) => ({
      question: questionFor(d),
      meta: `${providerLabel(d.source)} · edited ${timeAgo(d.last_edited_at ?? d.synced_at)}`,
      dot: "ink",
    }));
    const thisWeek = recent.filter((d) => Date.now() - edited(d) < WEEK_MS);
    const providers = Array.from(new Set(thisWeek.map((d) => providerLabel(d.source))));
    if (thisWeek.length > 0) {
      cards.push({
        question: "Summarize everything that changed in our docs this week",
        meta: `${providers.join(" + ")} · ${thisWeek.length} document${thisWeek.length === 1 ? "" : "s"} updated in 7 days`,
        dot: "accent",
        featured: true,
      });
    }
    return cards;
  }, [docs]);

  const chunkTotal = docs.reduce((sum, d) => sum + d.chunk_count, 0);
  const allDocsLoaded = Object.values(docTotals).reduce((a, b) => a + b, 0) <= docs.length;

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[1000px] flex-col gap-14 px-6 pt-16 pb-8 md:px-14 md:pt-[72px]">
      <div className="flex flex-col gap-[18px]">
        <span className="font-mono text-xs tracking-[0.1em] text-ink-500">
          {greetingWord().toUpperCase()}
          {firstName ? `, ${firstName.toUpperCase()}` : ""}
        </span>
        <h1 className="max-w-[880px] font-serif text-[44px] leading-[1.04] tracking-[-0.02em] text-ink-100 md:text-[64px]">
          Ask your workspace.
          <br />
          <span className="hl italic">Every answer</span> shows its sources.
        </h1>
      </div>

      <div className="max-w-[880px]">
        <ChatInput
          variant="hero"
          disabled={disabled}
          onSend={onSend}
          adornment={
            connected.length > 0 ? (
              <span className="hidden shrink-0 items-center gap-1.5 sm:flex">
                {connected.map((c) => (
                  <span
                    key={c.provider}
                    className="flex h-[30px] items-center gap-1.5 rounded-full border border-ink-700 px-3 text-xs text-ink-300"
                  >
                    <SourceIcon source={c.provider} className="h-3 w-3" />
                    {providerLabel(c.provider)}
                  </span>
                ))}
              </span>
            ) : null
          }
        />
        {error && (
          <div className="mt-4 rounded-xl border border-error_subtle bg-error-primary px-4 py-2.5 text-sm text-error-primary">
            {error}
          </div>
        )}
      </div>

      <section className="flex flex-col gap-4" aria-labelledby="insights-heading">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="insights-heading" className="font-mono text-[11px] tracking-[0.08em] text-ink-500">
            {docs.length > 0 ? "FROM WHAT CHANGED THIS WEEK" : "SUGGESTED QUESTIONS"}
          </h2>
          {loadedAt && <span className="text-xs text-ink-500">refreshed {timeAgo(loadedAt)}</span>}
        </div>
        <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
          {insights.map((card) => {
            const className = `flex flex-col gap-[22px] rounded-[18px] border px-[22px] pt-[22px] pb-[18px] text-left transition duration-200 hover:-translate-y-0.5 hover:shadow-[0_18px_36px_-24px_rgb(0_0_0/0.45)] disabled:opacity-60 ${
              card.featured
                ? "border-ink-100 bg-accent text-accent-ink"
                : "border-ink-800 bg-ink-900 text-ink-100 hover:border-ink-700"
            }`;
            const body = (
              <>
              <span className="font-serif text-[26px] leading-[1.2]">{card.question}</span>
              <span
                className={`flex items-center gap-2.5 font-mono text-[11.5px] ${
                  card.featured ? "text-accent-ink" : "text-ink-400"
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${card.featured ? "bg-accent-ink" : "bg-ink-100"}`}
                  aria-hidden="true"
                />
                {card.meta}
              </span>
              </>
            );
            return card.href ? (
              <Link key={card.question} href={card.href} className={className}>
                {body}
              </Link>
            ) : (
              <button key={card.question} type="button" disabled={disabled} onClick={() => onSend(card.question)} className={className}>
                {body}
              </button>
            );
          })}
        </div>
      </section>

      <div className="flex-1" />

      <footer className="flex flex-wrap gap-x-7 gap-y-2 border-t border-ink-800 pt-5 text-[13px] text-ink-400">
        {connected.length === 0 && (
          <span className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-ink-600" aria-hidden="true" />
            No sources connected yet — add one under Connections
          </span>
        )}
        {connected.map((c) => {
          const healthy = c.last_sync_status !== "failed" && c.last_sync_status !== "dead_lettered";
          const total = docTotals[c.provider];
          return (
            <span key={c.provider} className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${healthy ? "bg-success-solid" : "bg-error-solid"}`} aria-hidden="true" />
              {providerLabel(c.provider)}
              {total !== undefined && ` · ${total} ${c.provider === "jira" ? "issues" : "docs"}`} · synced{" "}
              {timeAgo(c.last_synced_at)}
            </span>
          );
        })}
        <span className="flex-1" />
        {docs.length > 0 && allDocsLoaded && <span>{chunkTotal.toLocaleString()} chunks indexed</span>}
      </footer>
    </div>
  );
}
