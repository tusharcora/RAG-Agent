import { StatusBadge } from "@/components/StatusBadge";
import type { EventLogEntry } from "@/lib/types";

export function EventTable({ events }: { events: EventLogEntry[] }) {
  if (events.length === 0) {
    return <p className="px-1 py-8 text-center text-sm text-slate-500">No events yet — trigger a sync to see activity here.</p>;
  }

  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
          <th className="py-2 pr-3 font-medium">Time</th>
          <th className="py-2 pr-3 font-medium">Routing key</th>
          <th className="py-2 pr-3 font-medium">Status</th>
          <th className="py-2 pr-3 font-medium">Dedupe key</th>
          <th className="py-2 pr-3 font-medium">Trace ID</th>
          <th className="py-2 pr-3 font-medium">Error</th>
        </tr>
      </thead>
      <tbody>
        {events.map((e) => (
          <tr key={e.id} className="border-b border-slate-900">
            <td className="whitespace-nowrap py-2.5 pr-3 text-slate-400">{new Date(e.created_at).toLocaleTimeString()}</td>
            <td className="py-2.5 pr-3 font-mono text-xs text-slate-300">{e.routing_key}</td>
            <td className="py-2.5 pr-3">
              <StatusBadge status={e.status} />
            </td>
            <td className="max-w-xs truncate py-2.5 pr-3 font-mono text-xs text-slate-500">{e.dedupe_key}</td>
            <td className="py-2.5 pr-3">
              {e.trace_id ? (
                <button
                  type="button"
                  onClick={() => navigator.clipboard?.writeText(e.trace_id ?? "")}
                  className="font-mono text-xs text-slate-500 hover:text-slate-300"
                  title="Copy trace id"
                >
                  {e.trace_id.slice(0, 10)}…
                </button>
              ) : (
                <span className="text-xs text-slate-700">—</span>
              )}
            </td>
            <td className="max-w-xs truncate py-2.5 pr-3 text-xs text-red-400">{e.error ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
