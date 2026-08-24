export function StatTile({ label, value, accent = "coral" }: { label: string; value: number | string; accent?: "coral" | "gold" }) {
  return (
    <div className="cyber-chamfer-sm flex items-center gap-2.5 rounded-xl border border-ink-800 bg-ink-900/60 px-3.5 py-2">
      <span className={`h-2 w-2 rounded-full ${accent === "coral" ? "bg-coral-500" : "bg-gold-400"}`} />
      <span className="text-sm font-semibold text-ink-100">{value}</span>
      <span className="text-ink-500">{label}</span>
    </div>
  );
}
