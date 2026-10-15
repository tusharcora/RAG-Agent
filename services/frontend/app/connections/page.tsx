"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle } from "@untitledui/icons";
import { getConnections, getDlqEvents, getDocuments, getEvents, getVoyageUsage } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import { IS_DESKTOP, useAuth } from "@/lib/auth-context";
import { toast } from "@/lib/toast";
import { providerLabel, timeAgo } from "@/lib/format";
import { ConnectionCard } from "@/components/connections/ConnectionCard";
import { PageHeader } from "@/components/PageHeader";
import { ConnectionCardSkeleton } from "@/components/base/skeleton/skeleton";
import type { ConnectionStatus, DlqEvent, DocumentSummary, EventLogEntry, VoyageUsage } from "@/lib/types";

const ROUTING_KEYS: Record<string, string> = {
  notion: "rag.notion_page_updated",
  jira: "rag.jira_issue_updated",
};

export default function ConnectionsPage() {
  return (
    <Suspense fallback={null}>
      <ConnectionsPageContent />
    </Suspense>
  );
}

function ConnectionsPageContent() {
  const [connections, setConnections] = useState<ConnectionStatus[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [pollFast, setPollFast] = useState(false);
  const [voyageUsage, setVoyageUsage] = useState<VoyageUsage | null>(null);
  const [docTotals, setDocTotals] = useState<Record<string, number>>({});
  const [events, setEvents] = useState<EventLogEntry[]>([]);
  const [recent, setRecent] = useState<DocumentSummary[]>([]);
  const [dlq, setDlq] = useState<DlqEvent[]>([]);
  const searchParams = useSearchParams();
  const justConnected = searchParams.get("connected");
  const router = useRouter();
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  const refresh = useCallback(() => {
    getConnections()
      .then(async (res) => {
        setConnections(res);
        setLoaded(true);
        const totals = await Promise.all(
          res
            .filter((c) => c.connected)
            .map((c) =>
              getDocuments({ source: c.provider, limit: 1 })
                .then((r) => [c.provider, r.total] as const)
                .catch(() => [c.provider, 0] as const),
            ),
        );
        setDocTotals(Object.fromEntries(totals));
      })
      .catch(() => {});
    getEvents({ limit: 100 })
      .then(setEvents)
      .catch(() => {});
    getDocuments({ limit: 50 })
      .then((r) => {
        const synced = (d: DocumentSummary) => new Date(d.synced_at).getTime();
        setRecent([...r.items].sort((a, b) => synced(b) - synced(a)).slice(0, 6));
      })
      .catch(() => {});
  }, []);

  useEffect(refresh, [refresh]);

  // Guarded by a ref so React StrictMode's dev-only double effect run can't
  // toast the same connection twice.
  const announcedConnection = useRef<string | null>(null);
  useEffect(() => {
    if (!justConnected || announcedConnection.current === justConnected) return;
    announcedConnection.current = justConnected;
    toast.success(`${providerLabel(justConnected)} connected successfully.`);
    router.replace("/connections");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [justConnected]);

  // Billing and failure info only matter to admins, and fetch once on mount.
  useEffect(() => {
    if (!isAdmin) return;
    getVoyageUsage()
      .then(setVoyageUsage)
      .catch(() => {});
    getDlqEvents()
      .then(setDlq)
      .catch(() => {});
  }, [isAdmin]);

  // A sync only enqueues events, so poll faster for a while after "Sync now".
  useInterval(refresh, pollFast ? 3000 : null);

  const handleSynced = () => {
    setPollFast(true);
    setTimeout(() => setPollFast(false), 30000);
  };

  const runsFor = (provider: string) =>
    events
      .filter((e) => e.routing_key === ROUTING_KEYS[provider])
      .slice(0, 24)
      .reverse()
      .map((e) => e.status);

  return (
    <div className="mx-auto flex max-w-[1240px] flex-col gap-8 px-6 py-10 md:px-14">
      <PageHeader
        eyebrow="DATA SOURCES"
        title="Connections"
        description="Connect Notion and Jira, then sync content for retrieval."
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[repeat(auto-fit,minmax(340px,1fr))]">
        {!loaded ? (
          <>
            <ConnectionCardSkeleton />
            <ConnectionCardSkeleton />
          </>
        ) : (
          connections.map((c) => (
            <ConnectionCard
              key={c.provider}
              status={c}
              onSynced={handleSynced}
              isPolling={pollFast}
              docCount={docTotals[c.provider]}
              runs={runsFor(c.provider)}
            />
          ))
        )}

        {isAdmin && voyageUsage && !IS_DESKTOP && (
          <section className="flex flex-col gap-[22px] rounded-[20px] bg-inv-bg p-6 text-inv-ink">
            <span className="font-mono text-[11px] tracking-[0.1em] text-inv-ink2">EMBEDDING BUDGET · VOYAGE</span>
            <div className="flex items-baseline gap-2">
              <span className="font-serif text-[58px] leading-none">{formatTokens(voyageUsage.used)}</span>
              <span className="text-sm text-inv-ink2">of {formatTokens(voyageUsage.budget)} tokens</span>
            </div>
            <div className="flex flex-col gap-2.5">
              <div className="h-2.5 rounded-full bg-inv-track">
                <div
                  className="h-2.5 rounded-full bg-accent"
                  style={{ width: `${Math.min(100, Math.max(2, voyageUsage.percent))}%` }}
                />
              </div>
              <div className="flex justify-between font-mono text-[11px] text-inv-ink2">
                <span>{Math.round(voyageUsage.percent)}% used</span>
                <span>free tier</span>
              </div>
            </div>
            <p className="text-[13px] leading-relaxed text-inv-ink3">
              Unchanged documents are skipped by content hash, so re-syncing only embeds what actually changed.
            </p>
          </section>
        )}
      </div>

      {isAdmin && dlq.length > 0 && (
        <section className="flex flex-wrap items-center gap-[18px] rounded-[18px] border border-bad-line bg-bad-bg px-[22px] py-[18px]">
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-error-solid text-white">
            <AlertTriangle className="h-[18px] w-[18px]" aria-hidden="true" />
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="text-[15px] font-medium text-ink-100">
              {dlq.length} item{dlq.length === 1 ? "" : "s"} failed after every retry
            </span>
            <span className="truncate text-[13px] text-ink-400">
              {dlq[0].routing_key} · {dlq[0].error ?? "no error message"} · {timeAgo(dlq[0].created_at)}
            </span>
          </div>
          <Link
            href="/activity"
            className="flex h-10 items-center rounded-full bg-ink-100 px-4 text-sm font-medium text-ink-950 transition hover:bg-ink-200"
          >
            Review and retry
          </Link>
        </section>
      )}

      <section className="flex flex-col gap-3.5" aria-labelledby="recent-heading">
        <div className="flex items-baseline justify-between">
          <h2 id="recent-heading" className="font-mono text-[11px] tracking-[0.08em] text-ink-500">
            RECENTLY INGESTED
          </h2>
          <Link href="/knowledge-base" className="text-xs text-ink-400 underline-offset-2 hover:text-ink-100 hover:underline">
            Open knowledge base
          </Link>
        </div>
        <div className="overflow-x-auto rounded-2xl border border-ink-800 bg-ink-900">
          {recent.length === 0 ? (
            <p className="px-5 py-6 text-sm text-ink-500">Nothing ingested yet. Connect a source and press Sync now.</p>
          ) : (
            <div className="min-w-[640px]">
              {recent.map((d) => (
                <a
                  key={d.id}
                  href={d.url}
                  target="_blank"
                  rel="noreferrer"
                  className="grid grid-cols-[minmax(0,1fr)_100px_150px_110px] items-center gap-4 border-b border-ink-800 px-5 py-3.5 text-sm transition last:border-b-0 hover:bg-ink-850"
                >
                  <span className="truncate text-ink-100">{d.title}</span>
                  <span className="font-mono text-xs text-ink-400">{providerLabel(d.source)}</span>
                  <span>
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs ${
                        d.excluded_from_retrieval ? "bg-ink-800 text-ink-400" : "bg-ok-bg text-success-primary"
                      }`}
                    >
                      {d.excluded_from_retrieval ? "Excluded" : `${d.chunk_count} chunks`}
                    </span>
                  </span>
                  <span className="text-right font-mono text-xs text-ink-400">{timeAgo(d.synced_at)}</span>
                </a>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

// Formats a raw token count as e.g. "12.4M" / "850K".
function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
