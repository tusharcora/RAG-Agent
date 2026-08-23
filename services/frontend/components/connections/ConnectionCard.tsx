"use client";

import { useState } from "react";
import { API_BASE, syncProvider } from "@/lib/api";
import { SourceIcon } from "@/components/icons/SourceIcon";
import type { ConnectionStatus } from "@/lib/types";

const LABELS: Record<string, string> = { notion: "Notion", jira: "Jira" };

export function ConnectionCard({ status, onSynced }: { status: ConnectionStatus; onSynced: () => void }) {
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);

  const handleSync = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const result = await syncProvider(status.provider);
      setSyncResult(`Queued ${result.published} item${result.published === 1 ? "" : "s"}${result.truncated ? " (capped)" : ""}`);
      onSynced();
    } catch {
      setSyncResult("Sync failed to start — check the API logs.");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
      <div className="mb-4 flex items-center gap-2">
        <SourceIcon source={status.provider} className="h-5 w-5 text-slate-300" />
        <h2 className="text-base font-semibold text-slate-100">{LABELS[status.provider] ?? status.provider}</h2>
        <span
          className={`ml-auto rounded-full px-2 py-0.5 text-xs font-medium ${
            status.connected ? "bg-green-500/15 text-green-300" : "bg-slate-700/50 text-slate-400"
          }`}
        >
          {status.connected ? "Connected" : "Not connected"}
        </span>
      </div>

      {status.connected ? (
        <div className="space-y-1 text-sm">
          <Row label="Workspace" value={status.workspace_name ?? "—"} />
          <Row label="Last synced" value={formatTimestamp(status.last_synced_at)} />
        </div>
      ) : (
        <p className="text-sm text-slate-500">Connect to start syncing content for retrieval.</p>
      )}

      <div className="mt-4">
        {status.connected ? (
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-50"
          >
            {syncing ? "Syncing…" : "Sync now"}
          </button>
        ) : (
          <a
            href={`${API_BASE}/oauth/${status.provider}/authorize`}
            className="inline-block rounded-md bg-slate-100 px-3 py-1.5 text-sm font-medium text-slate-900 transition hover:bg-white"
          >
            Connect
          </a>
        )}
        {syncResult && <p className="mt-2 text-xs text-slate-500">{syncResult}</p>}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-slate-500">{label}</span>
      <span className="truncate text-slate-300">{value}</span>
    </div>
  );
}

function formatTimestamp(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
}
