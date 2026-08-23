"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getConnections } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import { ConnectionCard } from "@/components/connections/ConnectionCard";
import type { ConnectionStatus } from "@/lib/types";

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
  const searchParams = useSearchParams();
  const justConnected = searchParams.get("connected");

  const refresh = useCallback(() => {
    getConnections()
      .then(setConnections)
      .catch(() => {});
  }, []);

  useEffect(refresh, [refresh]);

  // A sync only enqueues events — last_synced_at won't move until the worker
  // actually processes them, so poll briefly and faster right after "Sync now".
  useInterval(refresh, pollFast ? 3000 : null);

  const handleSynced = () => {
    setPollFast(true);
    setTimeout(() => setPollFast(false), 30000);
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Connections</h1>
      <p className="mb-6 text-sm text-slate-500">Connect Notion and Jira, then sync content for retrieval.</p>

      {justConnected && (
        <div className="mb-6 rounded-md border border-green-900/50 bg-green-950/30 px-3 py-2 text-sm text-green-300">
          {justConnected === "notion" ? "Notion" : "Jira"} connected successfully.
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
