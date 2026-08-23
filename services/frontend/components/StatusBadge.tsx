const STYLES: Record<string, string> = {
  received: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  processing: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  succeeded: "bg-green-500/15 text-green-300 ring-green-500/30",
  failed: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  dead_lettered: "bg-red-500/15 text-red-300 ring-red-500/30",
};

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? STYLES.received;
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}>
      {status}
    </span>
  );
}
