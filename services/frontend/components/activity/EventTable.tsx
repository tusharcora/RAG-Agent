"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { SortDescriptor } from "react-aria-components";
import { StatusBadge } from "@/components/StatusBadge";
import { Table } from "@/components/application/table/table";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import type { EventLogEntry } from "@/lib/types";

export function EventTable({ events }: { events: EventLogEntry[] }) {
  const copy = useCopyToClipboard();
  const [sortDescriptor, setSortDescriptor] = useState<SortDescriptor>({ column: "time", direction: "descending" });

  // Diffs each poll tick against previously-seen ids so genuinely-new rows
  // can flash — most of the perceived benefit of real push, with none of the
  // backend infra a live SSE feed would need.
  const seenIds = useRef<Set<string> | null>(null);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    const currentIds = new Set(events.map((e) => e.id));
    if (seenIds.current) {
      const arrived = new Set([...currentIds].filter((id) => !seenIds.current!.has(id)));
      if (arrived.size > 0) {
        setNewIds(arrived);
        const timer = setTimeout(() => setNewIds(new Set()), 1500);
        seenIds.current = currentIds;
        return () => clearTimeout(timer);
      }
    }
    seenIds.current = currentIds;
  }, [events]);

  const sorted = useMemo(() => {
    const dir = sortDescriptor.direction === "ascending" ? 1 : -1;
    return [...events].sort((a, b) => {
      switch (sortDescriptor.column) {
        case "status":
          return dir * a.status.localeCompare(b.status);
        case "routing_key":
          return dir * a.routing_key.localeCompare(b.routing_key);
        case "time":
        default:
          return dir * (new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
      }
    });
  }, [events, sortDescriptor]);

  if (events.length === 0) {
    return <p className="px-1 py-8 text-center text-sm text-ink-500">No events yet — trigger a sync to see activity here.</p>;
  }

  return (
    <Table aria-label="Activity feed" size="sm" sortDescriptor={sortDescriptor} onSortChange={setSortDescriptor}>
      <Table.Header>
        <Table.Head id="time" label="Time" isRowHeader allowsSorting />
        <Table.Head id="routing_key" label="Routing key" allowsSorting />
        <Table.Head id="status" label="Status" allowsSorting />
        <Table.Head id="dedupe_key" label="Dedupe key" />
        <Table.Head id="trace_id" label="Trace ID" />
        <Table.Head id="error" label="Error" />
      </Table.Header>
      <Table.Body items={sorted}>
        {(e) => (
          <Table.Row id={e.id} className={`transition-colors duration-1000 ${newIds.has(e.id) ? "bg-gold-400/10" : ""}`}>
            <Table.Cell className="whitespace-nowrap">{new Date(e.created_at).toLocaleTimeString()}</Table.Cell>
            <Table.Cell className="font-mono text-xs whitespace-nowrap">{e.routing_key}</Table.Cell>
            <Table.Cell>
              <StatusBadge status={e.status} />
            </Table.Cell>
            <Table.Cell className="max-w-xs truncate font-mono text-xs">{e.dedupe_key}</Table.Cell>
            <Table.Cell>
              {e.trace_id ? (
                <button
                  type="button"
                  onClick={() => copy(e.trace_id ?? "", "Trace ID copied")}
                  className="font-mono text-xs text-quaternary hover:text-tertiary"
                  title="Copy trace id"
                >
                  {e.trace_id.slice(0, 10)}…
                </button>
              ) : (
                <span className="text-xs text-ink-700">—</span>
              )}
            </Table.Cell>
            <Table.Cell className="max-w-xs truncate text-xs text-error-primary">{e.error ?? ""}</Table.Cell>
          </Table.Row>
        )}
      </Table.Body>
    </Table>
  );
}
