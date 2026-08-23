const STYLES: Record<string, string> = {
  received: "bg-ink-700/40 text-ink-300 ring-ink-600/40",
  processing: "bg-gold-400/15 text-gold-300 ring-gold-400/30",
  succeeded: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  failed: "bg-coral-500/15 text-coral-300 ring-coral-500/30",
  dead_lettered: "bg-coral-700/25 text-coral-200 ring-coral-600/40",
};

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? STYLES.received;
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}>
      {status}
    </span>
  );
}
