"use client";

import { useCallback, useEffect, useState } from "react";
import { getEvents } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import { useAuth } from "@/lib/auth-context";
import { EventTable } from "@/components/activity/EventTable";
import { DlqPanel } from "@/components/activity/DlqPanel";
import { StatTile } from "@/components/StatTile";
import { TableCard } from "@/components/application/table/table";
import { NativeSelect } from "@/components/base/select/select-native";
import { TableRowsSkeleton } from "@/components/base/skeleton/skeleton";
import type { EventLogEntry } from "@/lib/types";

const STATUSES = ["received", "processing", "succeeded", "failed", "dead_lettered"];
const STATUS_OPTIONS = [{ label: "All statuses", value: "" }, ...STATUSES.map((s) => ({ label: s, value: s }))];

export default function ActivityPage() {
  const [events, setEvents] = useState<EventLogEntry[]>([]);
  const [status, setStatus] = useState("");
  const [loaded, setLoaded] = useState(false);
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  const refresh = useCallback(() => {
    getEvents({ limit: 100, status: status || undefined })
      .then((res) => {
        setEvents(res);
        setLoaded(true);
      })
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
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <StatTile label="events" value={events.length} accent="gold" />
        <StatTile label="succeeded" value={succeeded} accent="coral" />
        <StatTile label="failed / dead-lettered" value={failed} accent="coral" />
      </div>

      <TableCard.Root>
        <TableCard.Header
          title="Events"
          description="Sync and embed events, newest first."
          contentTrailing={
            <NativeSelect
              size="sm"
              options={STATUS_OPTIONS}
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="w-40"
            />
          }
        />
        <div className="overflow-x-auto">
          {loaded ? <EventTable events={events} /> : <TableRowsSkeleton columns={6} />}
        </div>
      </TableCard.Root>

      {isAdmin && <DlqPanel />}
    </div>
  );
}
