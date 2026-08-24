"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, SearchLg } from "@untitledui/icons";
import { getSessions } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import type { SessionSummary } from "@/lib/types";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";

export function SessionSidebar({
  activeSessionId,
  onSelect,
  onNewChat,
  refreshSignal,
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
            <li key={s.session_id}>
              <button
                type="button"
                onClick={() => onSelect(s.session_id)}
                className={`cyber-chamfer-sm w-full truncate rounded-lg px-2 py-2 text-left text-sm transition ${
                  s.session_id === activeSessionId
                    ? "bg-coral-500/15 text-coral-200"
                    : "text-ink-400 hover:bg-ink-800/60 hover:text-ink-200"
                }`}
                title={s.preview ?? undefined}
              >
                {s.preview || "Untitled conversation"}
                <span className="ml-1 text-xs text-ink-600">· {s.turn_count} turns</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
