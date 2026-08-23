"use client";

import { useCallback, useEffect, useState } from "react";
import { getEvents } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import { EventTable } from "@/components/activity/EventTable";
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

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="mb-1 text-xl font-semibold text-slate-100">Activity</h1>
          <p className="text-sm text-slate-500">Live feed of sync/embed events from the shared event_log table.</p>
        </div>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 focus:border-sky-600 focus:outline-none"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/40 p-2">
        <EventTable events={events} />
      </div>
    </div>
  );
}
