"use client";

import { useEffect, useRef, useState } from "react";
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
import { SearchLg } from "@untitledui/icons";
import { useAuth } from "@/lib/auth-context";
import { toast } from "@/lib/toast";
import type { ConnectionPreview, ConnectionStatus, OrgMember } from "@/lib/types";
import { Badge } from "@/components/base/badges/badges";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";

const LABELS: Record<string, string> = { notion: "Notion", jira: "Jira" };

export function ConnectionCard({
  status,
  onSynced,
  isPolling,
  docCount,
  runs,
}: {
  status: ConnectionStatus;
  onSynced: () => void;
  docCount?: number;
  // Statuses of this provider's most recent sync events, oldest first.
  runs?: string[];
  // True during the 3s-for-30s fast poll app/connections/page.tsx runs right
  // after a manual sync — shown as a pulse badge so the wait has a visible
  // "still working" signal without needing a dedicated push channel.
  isPolling?: boolean;
}) {
  const [syncing, setSyncing] = useState(false);
  const [preview, setPreview] = useState<ConnectionPreview | null>(null);
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  // Flashes the "Last synced" row when its value actually changes between
  // polls, so a completed sync is visible even without watching the badge.
  const prevSyncedAt = useRef(status.last_synced_at);
  const [justUpdated, setJustUpdated] = useState(false);
  useEffect(() => {
    if (prevSyncedAt.current !== status.last_synced_at) {
      prevSyncedAt.current = status.last_synced_at;
      setJustUpdated(true);
      const timer = setTimeout(() => setJustUpdated(false), 1500);
      return () => clearTimeout(timer);
    }
  }, [status.last_synced_at]);

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
    try {
      const result = await syncProvider(status.provider);
      const removed = result.removed ?? 0;
      toast.success(
        `Queued ${result.published} item${result.published === 1 ? "" : "s"}${result.truncated ? " (capped)" : ""}`,
        removed > 0
          ? `Removed ${removed} that ${removed === 1 ? "is" : "are"} no longer shared or no longer exist${removed === 1 ? "s" : ""}.`
          : undefined,
      );
      onSynced();
    } catch {
      toast.error("Sync failed to start", "Check the API logs.");
    } finally {
      setSyncing(false);
    }
  };

  const healthy = status.last_sync_status !== "failed" && status.last_sync_status !== "dead_lettered";
  const statusLabel = !status.connected
    ? "NOT CONNECTED"
    : !healthy
      ? "LAST SYNC FAILED"
      : status.dead_lettered_count_24h > 0
        ? `${status.dead_lettered_count_24h} FAILED ITEM${status.dead_lettered_count_24h === 1 ? "" : "S"}`
        : "HEALTHY";
  const statusTone = !status.connected ? "text-ink-500" : healthy && status.dead_lettered_count_24h === 0 ? "text-success-primary" : "text-error-primary";

  return (
    <section className="flex min-w-0 flex-col gap-[22px] rounded-[20px] border border-ink-800 bg-ink-900 p-6">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2.5 text-[17px] font-semibold text-ink-100">
          <SourceIcon source={status.provider} className="h-[18px] w-[18px]" />
          {LABELS[status.provider] ?? status.provider}
          {status.workspace_name && <span className="truncate text-sm font-normal text-ink-500">· {status.workspace_name}</span>}
        </span>
        <span className={`shrink-0 font-mono text-[11px] tracking-[0.06em] ${statusTone}`}>{statusLabel}</span>
      </div>

      {status.connected ? (
        <>
          <div className="grid grid-cols-3 gap-4">
            <BigStat value={docCount === undefined ? "—" : docCount.toLocaleString()} label={status.provider === "jira" ? "issues" : "pages"} />
            <BigStat value={preview ? `${preview.visible_count}${preview.truncated ? "+" : ""}` : "—"} label="visible to integration" />
            <BigStat value={shortAgo(status.last_synced_at)} label="since last sync" highlighted={justUpdated} />
          </div>

          {runs && runs.length > 0 && (
            <div className="flex flex-col gap-2">
              <div className="flex h-9 items-end gap-[3px]" aria-label="Recent sync events">
                {runs.map((r, i) => (
                  <div
                    key={i}
                    title={r}
                    className={`flex-1 rounded-[3px] ${
                      r === "succeeded"
                        ? "h-full bg-ink-100"
                        : r === "failed" || r === "dead_lettered"
                          ? "h-full bg-error-solid"
                          : "h-1/2 bg-ink-700"
                    }`}
                  />
                ))}
              </div>
              <div className="flex justify-between font-mono text-[11px] text-ink-500">
                <span>older</span>
                <span>recent sync events</span>
                <span>now</span>
              </div>
            </div>
          )}

          <div className="space-y-1.5 border-t border-ink-800 pt-4 text-sm">
            <Row label="Last synced" value={formatTimestamp(status.last_synced_at)} highlighted={justUpdated} />
            <Row label="Visibility" value={status.visibility_mode === "restricted" ? "Restricted" : "Org-wide"} />
            {preview && preview.visible_count === 0 && (
              <Row label="Permissions" value="0 pages shared — check the integration's access" />
            )}
          </div>
        </>
      ) : (
        <p className="text-sm text-ink-400">Connect to start syncing content for retrieval.</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {status.connected ? (
          <>
            {status.provider === "notion" && (
              <p className="w-full text-xs text-ink-500">
                Notion only syncs pages shared with the integration. New pages inside a shared page are included
                automatically.
              </p>
            )}
            <Button color="secondary" size="sm" isLoading={syncing} onPress={handleSync}>
              Sync now
            </Button>
            {status.provider === "notion" && isAdmin && (
              // Re-running Notion's OAuth flow reopens its page picker; the
              // callback updates this org's existing connection in place.
              <Button
                color="tertiary"
                size="sm"
                href={`${API_BASE}/oauth/notion/authorize`}
              >
                Choose pages in Notion
              </Button>
            )}
            {isPolling && (
              <Badge type="pill-color" color="warning" size="sm" className="animate-pulse">
                Syncing…
              </Badge>
            )}
          </>
        ) : (
          <Button color="primary" size="sm" href={`${API_BASE}/oauth/${status.provider}/authorize`}>
            Connect
          </Button>
        )}
      </div>

      {status.connected && isAdmin && status.id && <AccessControl connectionId={status.id} status={status} />}
    </section>
  );
}

function BigStat({ value, label, highlighted }: { value: string; label: string; highlighted?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span
        className={`truncate font-serif text-[40px] leading-none transition-colors duration-1000 ${
          highlighted ? "hl text-ink-100" : "text-ink-100"
        }`}
      >
        {value}
      </span>
      <span className="text-xs text-ink-400">{label}</span>
    </div>
  );
}

function shortAgo(value: string | null): string {
  if (!value) return "—";
  const min = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  if (min < 60) return `${min}m`;
  const h = Math.round(min / 60);
  if (h < 48) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

function AccessControl({ connectionId, status }: { connectionId: string; status: ConnectionStatus }) {
  const [mode, setMode] = useState(status.visibility_mode ?? "org_wide");
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [memberSearch, setMemberSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const filteredMembers = members.filter((m) => {
    const q = memberSearch.trim().toLowerCase();
    if (!q) return true;
    return (m.display_name?.toLowerCase().includes(q) ?? false) || m.email.toLowerCase().includes(q);
  });

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
    <div className="border-t border-ink-800 pt-4">
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
                mode === "org_wide" ? "bg-ink-100 text-ink-950" : "text-ink-400 hover:text-ink-200"
              }`}
            >
              Org-wide
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => handleModeChange("restricted")}
              className={`rounded-full px-2.5 py-1 transition ${
                mode === "restricted" ? "bg-ink-100 text-ink-950" : "text-ink-400 hover:text-ink-200"
              }`}
            >
              Restricted
            </button>
          </div>

          {mode === "restricted" && (
            <div className="space-y-2">
              {members.length > 5 && (
                <Input
                  icon={SearchLg}
                  size="sm"
                  placeholder="Search members..."
                  value={memberSearch}
                  onChange={setMemberSearch}
                />
              )}
              <div className="max-h-40 space-y-1 overflow-y-auto rounded-xl border border-ink-800 p-2">
                {filteredMembers.map((m) => (
                  <label key={m.user_id} className="flex items-center gap-2 text-xs text-ink-300">
                    <input
                      type="checkbox"
                      checked={selected.has(m.user_id)}
                      onChange={() => toggleMember(m.user_id)}
                      className="accent-ink-100"
                    />
                    {m.display_name || m.email}
                  </label>
                ))}
                {members.length === 0 && <p className="text-xs text-ink-600">No org members found.</p>}
                {members.length > 0 && filteredMembers.length === 0 && (
                  <p className="text-xs text-ink-600">No members match.</p>
                )}
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

function Row({ label, value, highlighted }: { label: string; value: string; highlighted?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-ink-500">{label}</span>
      <span
        className={`-mx-1 truncate rounded px-1 transition-colors duration-1000 ${
          highlighted ? "bg-accent text-accent-ink" : "text-ink-300"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

function formatTimestamp(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
}
