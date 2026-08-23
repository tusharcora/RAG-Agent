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
    <div className="rounded-2xl border border-ink-800 bg-ink-900/60 p-5 shadow-panel transition hover:border-ink-700">
      <div className="mb-4 flex items-center gap-2.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-ink-800 text-ink-200">
          <SourceIcon source={status.provider} className="h-4.5 w-4.5" />
        </span>
        <h2 className="text-base font-semibold text-ink-100">{LABELS[status.provider] ?? status.provider}</h2>
        <span
          className={`ml-auto rounded-full px-2 py-0.5 text-xs font-medium ${
            status.connected ? "bg-emerald-500/15 text-emerald-300" : "bg-ink-700/50 text-ink-400"
          }`}
        >
          {status.connected ? "Connected" : "Not connected"}
        </span>
      </div>

      {status.connected ? (
        <div className="space-y-1.5 text-sm">
          <Row label="Workspace" value={status.workspace_name ?? "—"} />
          <Row label="Last synced" value={formatTimestamp(status.last_synced_at)} />
          <Row label="Visibility" value={status.visibility_mode === "restricted" ? "Restricted" : "Org-wide"} />
        </div>
      ) : (
        <p className="text-sm text-ink-500">Connect to start syncing content for retrieval.</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {status.connected ? (
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="rounded-full bg-coral-500 px-3.5 py-1.5 text-sm font-medium text-white transition hover:bg-coral-400 disabled:opacity-50"
          >
            {syncing ? "Syncing…" : "Sync now"}
          </button>
        ) : (
          <a
            href={`${API_BASE}/oauth/${status.provider}/authorize`}
            className="inline-block rounded-full bg-gradient-to-r from-coral-500 to-gold-400 px-3.5 py-1.5 text-sm font-medium text-ink-950 transition hover:brightness-110"
          >
            Connect
          </a>
        )}
      </div>
      {syncResult && <p className="mt-2 text-xs text-ink-500">{syncResult}</p>}

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
    <div className="mt-4 border-t border-ink-800 pt-4">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="text-xs font-medium text-ink-400 transition hover:text-coral-300"
      >
        {expanded ? "Hide access settings" : "Manage access"}
      </button>

      {expanded && (
        <div className="mt-3 space-y-3">
          <div className="inline-flex rounded-full border border-ink-700 bg-ink-950/60 p-0.5 text-xs">
            <button
              type="button"
              disabled={saving}
              onClick={() => handleModeChange("org_wide")}
              className={`rounded-full px-2.5 py-1 transition ${
                mode === "org_wide" ? "bg-coral-500 text-white" : "text-ink-400 hover:text-ink-200"
              }`}
            >
              Org-wide
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => handleModeChange("restricted")}
              className={`rounded-full px-2.5 py-1 transition ${
                mode === "restricted" ? "bg-coral-500 text-white" : "text-ink-400 hover:text-ink-200"
              }`}
            >
              Restricted
            </button>
          </div>

          {mode === "restricted" && (
            <div className="space-y-2">
              <div className="max-h-40 space-y-1 overflow-y-auto rounded-xl border border-ink-800 p-2">
                {members.map((m) => (
                  <label key={m.user_id} className="flex items-center gap-2 text-xs text-ink-300">
                    <input
                      type="checkbox"
                      checked={selected.has(m.user_id)}
                      onChange={() => toggleMember(m.user_id)}
                      className="accent-coral-500"
                    />
                    {m.display_name || m.email}
                  </label>
                ))}
                {members.length === 0 && <p className="text-xs text-ink-600">No org members found.</p>}
              </div>
              <button
                type="button"
                onClick={saveMembers}
                disabled={saving}
                className="rounded-full bg-ink-700 px-2.5 py-1 text-xs font-medium text-ink-100 transition hover:bg-ink-600 disabled:opacity-50"
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
      <span className="text-ink-500">{label}</span>
      <span className="truncate text-ink-300">{value}</span>
    </div>
  );
}

function formatTimestamp(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
}
