"use client";

import { useEffect, useState } from "react";
import {
  API_BASE,
  getConnectionMembers,
  getOrgMembers,
  setConnectionMembers,
  setConnectionVisibility,
  syncProvider,
} from "@/lib/api";
import { SourceIcon } from "@/components/icons/SourceIcon";
import { useAuth } from "@/lib/auth-context";
import type { ConnectionStatus, OrgMember } from "@/lib/types";

const LABELS: Record<string, string> = { notion: "Notion", jira: "Jira" };

export function ConnectionCard({ status, onSynced }: { status: ConnectionStatus; onSynced: () => void }) {
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

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
          <Row label="Visibility" value={status.visibility_mode === "restricted" ? "Restricted" : "Org-wide"} />
        </div>
      ) : (
        <p className="text-sm text-slate-500">Connect to start syncing content for retrieval.</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
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
      </div>
      {syncResult && <p className="mt-2 text-xs text-slate-500">{syncResult}</p>}

      {status.connected && isAdmin && status.id && <AccessControl connectionId={status.id} status={status} />}
    </div>
  );
}

function AccessControl({ connectionId, status }: { connectionId: string; status: ConnectionStatus }) {
  const [mode, setMode] = useState(status.visibility_mode ?? "org_wide");
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    getOrgMembers()
      .then(setMembers)
      .catch(() => {});
    getConnectionMembers(connectionId)
      .then((ids) => setSelected(new Set(ids)))
      .catch(() => {});
  }, [expanded, connectionId]);

  async function handleModeChange(next: "org_wide" | "restricted") {
    setSaving(true);
    try {
      await setConnectionVisibility(connectionId, next);
      setMode(next);
    } finally {
      setSaving(false);
    }
  }

  function toggleMember(userId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }

  async function saveMembers() {
    setSaving(true);
    try {
      await setConnectionMembers(connectionId, Array.from(selected));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-4 border-t border-slate-800 pt-4">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="text-xs font-medium text-slate-400 hover:text-slate-200"
      >
        {expanded ? "Hide access settings" : "Manage access"}
      </button>

      {expanded && (
        <div className="mt-3 space-y-3">
          <div className="flex gap-2 text-xs">
            <button
              type="button"
              disabled={saving}
              onClick={() => handleModeChange("org_wide")}
              className={`rounded-md px-2 py-1 ${mode === "org_wide" ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-400"}`}
            >
              Org-wide
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => handleModeChange("restricted")}
              className={`rounded-md px-2 py-1 ${mode === "restricted" ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-400"}`}
            >
              Restricted
            </button>
          </div>

          {mode === "restricted" && (
            <div className="space-y-2">
              <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-slate-800 p-2">
                {members.map((m) => (
                  <label key={m.user_id} className="flex items-center gap-2 text-xs text-slate-300">
                    <input type="checkbox" checked={selected.has(m.user_id)} onChange={() => toggleMember(m.user_id)} />
                    {m.display_name || m.email}
                  </label>
                ))}
                {members.length === 0 && <p className="text-xs text-slate-600">No org members found.</p>}
              </div>
              <button
                type="button"
                onClick={saveMembers}
                disabled={saving}
                className="rounded-md bg-slate-700 px-2 py-1 text-xs font-medium text-slate-100 hover:bg-slate-600 disabled:opacity-50"
              >
                Save access list
              </button>
            </div>
          )}
        </div>
      )}
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
