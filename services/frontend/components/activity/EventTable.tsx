import { StatusBadge } from "@/components/StatusBadge";
import { Table } from "@/components/application/table/table";
import type { EventLogEntry } from "@/lib/types";

export function EventTable({ events }: { events: EventLogEntry[] }) {
  if (events.length === 0) {
    return <p className="px-1 py-8 text-center text-sm text-ink-500">No events yet — trigger a sync to see activity here.</p>;
  }

  return (
    <Table aria-label="Activity feed" size="sm">
      <Table.Header>
        <Table.Head id="time" label="Time" isRowHeader />
        <Table.Head id="routing_key" label="Routing key" />
        <Table.Head id="status" label="Status" />
        <Table.Head id="dedupe_key" label="Dedupe key" />
        <Table.Head id="trace_id" label="Trace ID" />
        <Table.Head id="error" label="Error" />
      </Table.Header>
      <Table.Body items={events}>
        {(e) => (
          <Table.Row id={e.id}>
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
                  onClick={() => navigator.clipboard?.writeText(e.trace_id ?? "")}
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
