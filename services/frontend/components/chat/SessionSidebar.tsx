"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, SearchLg, Trash01 } from "@untitledui/icons";
import { deleteSession, getSessions } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import type { SessionSummary } from "@/lib/types";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";

export function SessionSidebar({
  activeSessionId,
  onSelect,
  onNewChat,
  refreshSignal,
  onDeleted,
}: {
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onNewChat: () => void;
  // Bumped by the parent right when a question is sent, so a brand-new
  // conversation shows up here immediately instead of waiting up to 15s for
  // the next poll — the backend now records the session the instant a
  // question is sent (see query.py), so there's something to fetch by the
  // time this fires.
  refreshSignal?: number;
  // Called after a successful delete so the parent can start a new chat if
  // the conversation that just got removed was the one currently open.
  onDeleted?: (sessionId: string) => void;
}) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [search, setSearch] = useState("");
  // Debounced separately from `search` so typing doesn't fire a request per
  // keystroke — only the settled value (300ms of no typing) reaches the API.
  const [debouncedSearch, setDebouncedSearch] = useState("");

  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(id);
  }, [search]);

  const refresh = useCallback(() => {
    getSessions(debouncedSearch || undefined)
      .then(setSessions)
      .catch(() => {});
  }, [debouncedSearch]);

  useEffect(refresh, [refresh, refreshSignal]);
  useInterval(refresh, 15000);

  // Optimistic — a delete should feel instant, and this list is cheap to
  // refetch/restore if it fails. Reverted on failure the same way feedback
  // clicks are elsewhere in this app.
  const handleDelete = (sessionId: string) => {
    const previous = sessions;
    setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
    deleteSession(sessionId)
      .then(() => onDeleted?.(sessionId))
      .catch(() => setSessions(previous));
  };

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-ink-800 bg-ink-900/40">
      <div className="space-y-2 p-3">
        <Button color="secondary" size="sm" iconLeading={Plus} onPress={onNewChat} className="cyber-chamfer-sm w-full">
          New chat
        </Button>
        <Input
          icon={SearchLg}
          size="sm"
          placeholder="Search conversations..."
          value={search}
          onChange={setSearch}
        />
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        <p className="px-2 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-ink-500">
          Recent conversations
        </p>
        {sessions.length === 0 && (
          <p className="px-2 py-2 text-xs text-ink-600">
            {search ? "No conversations match." : "No conversations yet."}
          </p>
        )}
        <ul className="space-y-1">
          {sessions.map((s) => (
            <li key={s.session_id} className="group relative">
              <button
                type="button"
                onClick={() => onSelect(s.session_id)}
                className={`cyber-chamfer-sm w-full truncate rounded-lg py-2 pl-2 pr-8 text-left text-sm transition ${
                  s.session_id === activeSessionId
                    ? "bg-coral-500/15 text-coral-200"
                    : "text-ink-400 hover:bg-ink-800/60 hover:text-ink-200"
                }`}
                title={s.preview ?? undefined}
              >
                {s.preview || "Untitled conversation"}
                <span className="ml-1 text-xs text-ink-600">· {s.turn_count} turns</span>
              </button>
              {/* Hover-only — a permanently visible delete icon on every row is
                  just noise for something this destructive and infrequent. */}
              <button
                type="button"
                aria-label="Delete conversation"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(s.session_id);
                }}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-ink-600 opacity-0 transition hover:text-coral-400 group-hover:opacity-100"
              >
                <Trash01 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
