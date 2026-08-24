"use client";

import { useEffect, useState } from "react";
import {
  API_BASE,
  getConnectionMembers,
  getConnectionPreview,
  getOrgMembers,
  setConnectionMembers,
  setConnectionVisibility,
  syncProvider,
} from "@/lib/api";
import { SourceIcon } from "@/components/icons/SourceIcon";
import { useAuth } from "@/lib/auth-context";
import type { ConnectionPreview, ConnectionStatus, OrgMember } from "@/lib/types";
import { Badge } from "@/components/base/badges/badges";
import { Button } from "@/components/base/buttons/button";

const LABELS: Record<string, string> = { notion: "Notion", jira: "Jira" };

export function ConnectionCard({ status, onSynced }: { status: ConnectionStatus; onSynced: () => void }) {
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);
  const [preview, setPreview] = useState<ConnectionPreview | null>(null);
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  // Fetched once per connection, not on every connections-page poll — this
  // hits Notion/Jira's live API directly (see connections.py's /preview
  // docstring), so folding it into the existing 3s-for-30s poll would be
  // slow and rate-limit-hungry for no benefit. status.id is a stable
  // primitive, so this effect only re-runs when the connection itself
  // changes, not on every poll re-render.
  useEffect(() => {
    if (!status.connected || !status.id) {
      setPreview(null);
      return;
    }
    getConnectionPreview(status.id)
      .then(setPreview)
      .catch(() => setPreview(null));
  }, [status.connected, status.id]);

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
        <Badge type="pill-color" color={status.connected ? "success" : "gray"} size="sm" className="ml-auto">
          {status.connected ? "Connected" : "Not connected"}
        </Badge>
      </div>

      {status.connected ? (
        <div className="space-y-1.5 text-sm">
          <Row label="Workspace" value={status.workspace_name ?? "—"} />
          <Row label="Last synced" value={formatTimestamp(status.last_synced_at)} />
          <Row label="Visibility" value={status.visibility_mode === "restricted" ? "Restricted" : "Org-wide"} />
          {preview && (
            <div className="flex items-center justify-between gap-4">
              <span className="text-ink-500">Visible to integration</span>
              <Badge type="pill-color" color={preview.visible_count === 0 ? "warning" : "gray"} size="sm">
                {preview.visible_count === 0
                  ? "0 pages shared — check permissions"
                  : `${preview.visible_count}${preview.truncated ? "+" : ""} page${preview.visible_count === 1 ? "" : "s"}`}
              </Badge>
            </div>
          )}
        </div>
      ) : (
        <p className="text-sm text-ink-500">Connect to start syncing content for retrieval.</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {status.connected ? (
          <Button color="secondary" size="sm" isLoading={syncing} onPress={handleSync}>
            Sync now
          </Button>
        ) : (
          <Button color="primary" size="sm" href={`${API_BASE}/oauth/${status.provider}/authorize`}>
            Connect
          </Button>
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
      <Button color="link-color" size="sm" onPress={() => setExpanded((v) => !v)}>
        {expanded ? "Hide access settings" : "Manage access"}
      </Button>

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
              <Button color="secondary" size="sm" isLoading={saving} onPress={saveMembers}>
                Save access list
              </Button>
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
