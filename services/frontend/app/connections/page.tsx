"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getConnections, getVoyageUsage } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import { useAuth } from "@/lib/auth-context";
import { ConnectionCard } from "@/components/connections/ConnectionCard";
import { StatTile } from "@/components/StatTile";
import type { ConnectionStatus, VoyageUsage } from "@/lib/types";

export default function ConnectionsPage() {
  return (
    <Suspense fallback={null}>
      <ConnectionsPageContent />
    </Suspense>
  );
}

function ConnectionsPageContent() {
  const [connections, setConnections] = useState<ConnectionStatus[]>([]);
  const [pollFast, setPollFast] = useState(false);
  const [voyageUsage, setVoyageUsage] = useState<VoyageUsage | null>(null);
  const searchParams = useSearchParams();
  const justConnected = searchParams.get("connected");
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  const refresh = useCallback(() => {
    getConnections()
      .then(setConnections)
      .catch(() => {});
  }, []);

  useEffect(refresh, [refresh]);

  // Operational/billing info, not something that needs to poll — a manual
  // page reload is fine to see updated usage, so this fetches once on mount
  // rather than joining the connections poll above.
  useEffect(() => {
    if (!isAdmin) return;
    getVoyageUsage()
      .then(setVoyageUsage)
      .catch(() => {});
  }, [isAdmin]);

  // A sync only enqueues events — last_synced_at won't move until the worker
  // actually processes them, so poll briefly and faster right after "Sync now".
  useInterval(refresh, pollFast ? 3000 : null);

  const handleSynced = () => {
    setPollFast(true);
    setTimeout(() => setPollFast(false), 30000);
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-1 text-xl font-semibold text-ink-100">Connections</h1>
      <p className="mb-6 text-sm text-ink-500">Connect Notion and Jira, then sync content for retrieval.</p>

      {justConnected && (
        <div className="mb-6 rounded-xl border border-emerald-900/50 bg-emerald-950/30 px-3 py-2 text-sm text-emerald-300">
          {justConnected === "notion" ? "Notion" : "Jira"} connected successfully.
        </div>
      )}

      {isAdmin && voyageUsage && (
        <div className="mb-6 flex flex-wrap gap-3">
          <StatTile
            label="Voyage embedding tokens (free tier)"
            value={`${formatTokens(voyageUsage.used)} / ${formatTokens(voyageUsage.budget)}`}
            accent={voyageUsage.percent >= 90 ? "gold" : "coral"}
          />
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {connections.map((c) => (
          <ConnectionCard key={c.provider} status={c} onSynced={handleSynced} />
        ))}
      </div>
    </div>
  );
}

// Formats a raw token count as e.g. "12.4M" / "850K" — the free-tier budget
// is in the hundreds of millions, so the raw integer isn't scannable.
function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
