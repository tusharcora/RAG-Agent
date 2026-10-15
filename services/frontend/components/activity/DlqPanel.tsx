"use client";

import { useCallback, useEffect, useState } from "react";
import { getDlqEvents, redriveDlqEvent } from "@/lib/api";
import { useInterval } from "@/hooks/useInterval";
import { Button } from "@/components/base/buttons/button";
import { Table, TableCard } from "@/components/application/table/table";
import { toast } from "@/lib/toast";
import type { DlqEvent } from "@/lib/types";

// Owner/admin only — matches the ConnectionCard pattern (components/connections/ConnectionCard.tsx)
// for gating admin-only actions on `user.role`, and mirrors the API's require_role check.
export function DlqPanel() {
  const [events, setEvents] = useState<DlqEvent[]>([]);
  const [redrivenIds, setRedrivenIds] = useState<Set<string>>(new Set());
  const [redrivingId, setRedrivingId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getDlqEvents()
      .then(setEvents)
      .catch(() => {});
  }, []);

  useEffect(refresh, [refresh]);
  useInterval(refresh, 5000); // same cadence as the activity feed this panel sits next to

  async function handleRedrive(id: string) {
    setRedrivingId(id);
    try {
      await redriveDlqEvent(id);
      // The redrive creates a NEW event_log row and leaves this one's status
      // at dead_lettered (see dlq.py) — it never disappears from GET /dlq on
      // its own, so track "already redriven" client-side instead of waiting
      // on a poll that will never reflect it. This client-only Set is not
      // durable (a refresh, or a second admin, won't see it) — that needs a
      // backend field on the event, tracked separately.
      setRedrivenIds((prev) => new Set(prev).add(id));
      toast.success("Event redriven");
    } catch {
      // leave the row as-is — the button resets and the admin can retry
      toast.error("Couldn't redrive this event", "Please try again.");
    } finally {
      setRedrivingId(null);
    }
  }

  if (events.length === 0) return null;

  return (
    <TableCard.Root className="mt-6">
      <TableCard.Header
        title="Dead-lettered events"
        description="Events that failed processing after all retries — reprocess with the same payload."
      />
      <div className="overflow-x-auto">
        <Table aria-label="Dead-lettered events" size="sm">
          <Table.Header>
            <Table.Head id="time" label="Time" isRowHeader />
            <Table.Head id="routing_key" label="Routing key" />
            <Table.Head id="error" label="Error" />
            <Table.Head id="action" label="" />
          </Table.Header>
          <Table.Body items={events}>
            {(e) => (
              <Table.Row id={e.id}>
                <Table.Cell className="whitespace-nowrap">{new Date(e.created_at).toLocaleTimeString()}</Table.Cell>
                <Table.Cell className="font-mono text-xs whitespace-nowrap">{e.routing_key}</Table.Cell>
                <Table.Cell className="max-w-xs truncate text-xs text-error-primary">{e.error ?? ""}</Table.Cell>
                <Table.Cell className="text-right">
                  {redrivenIds.has(e.id) ? (
                    <span className="text-xs text-ink-500">Redriven</span>
                  ) : (
                    <Button
                      color="secondary"
                      size="sm"
                      isLoading={redrivingId === e.id}
                      onPress={() => handleRedrive(e.id)}
                    >
                      Retry
                    </Button>
                  )}
                </Table.Cell>
              </Table.Row>
            )}
          </Table.Body>
        </Table>
      </div>
    </TableCard.Root>
  );
}
