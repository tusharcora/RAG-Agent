"use client";

import { useCallback, useEffect, useState } from "react";
import { getEvents } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import { useAuth } from "@/lib/auth-context";
import { PageHeader } from "@/components/PageHeader";
import { EventTable } from "@/components/activity/EventTable";
import { DlqPanel } from "@/components/activity/DlqPanel";

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
    <div className="mx-auto flex max-w-[1240px] flex-col gap-8 px-6 py-10 md:px-14">
      <PageHeader
        eyebrow="LIVE FEED · REFRESHES EVERY 5S"
        title="Activity"
        description="Sync and embed events as the worker processes them."
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <BigNumber value={events.length} label="recent events" />
        <BigNumber value={succeeded} label="succeeded" />
        <BigNumber value={failed} label="failed or dead-lettered" alert={failed > 0} />
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

function BigNumber({ value, label, alert }: { value: number; label: string; alert?: boolean }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-[20px] border border-ink-800 bg-ink-900 px-6 py-5">
      <span className={`font-serif text-[48px] leading-none ${alert ? "text-error-primary" : "text-ink-100"}`}>{value}</span>
      <span className="text-[13px] text-ink-400">{label}</span>
    </div>
  );
}
