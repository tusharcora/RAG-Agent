"use client";

import { useCallback, useEffect, useState } from "react";
import { getEvents } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import { EventTable } from "@/components/activity/EventTable";
import { StatTile } from "@/components/StatTile";
import type { EventLogEntry } from "@/lib/types";

const STATUSES = ["received", "processing", "succeeded", "failed", "dead_lettered"];

export default function ActivityPage() {
  const [events, setEvents] = useState<EventLogEntry[]>([]);
  const [status, setStatus] = useState("");

  const refresh = useCallback(() => {
    getEvents({ limit: 100, status: status || undefined })
      .then(setEvents)
      .catch(() => {});
  }, [status]);

  useEffect(refresh, [refresh]);
  useInterval(refresh, 5000); // tightest poll interval — this is the "live feed"

  const succeeded = events.filter((e) => e.status === "succeeded").length;
  const failed = events.filter((e) => e.status === "failed" || e.status === "dead_lettered").length;

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="mb-1 text-xl font-semibold text-ink-100">Activity</h1>
          <p className="text-sm text-ink-500">Live feed of sync/embed events from the shared event_log table.</p>
        </div>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-xl border border-ink-700 bg-ink-950 px-3 py-1.5 text-sm text-ink-100 focus:border-coral-500 focus:outline-none"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <StatTile label="events" value={events.length} accent="gold" />
        <StatTile label="succeeded" value={succeeded} accent="coral" />
        <StatTile label="failed / dead-lettered" value={failed} accent="coral" />
      </div>

      <div className="overflow-x-auto rounded-2xl border border-ink-800 bg-ink-900/40 p-2 shadow-panel">
        <EventTable events={events} />
      </div>
    </div>
  );
}
