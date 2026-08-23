import { SourceIcon } from "@/components/icons/SourceIcon";
import type { Source } from "@/lib/types";

export function SourcesPanel({
  sources,
  citedIndices,
  isStreaming,
}: {
  sources: Source[];
  citedIndices: number[] | null;
  isStreaming: boolean;
}) {
  if (sources.length === 0) {
    return (
      <aside className="w-80 shrink-0 border-l border-slate-800 bg-slate-900/40 p-4">
        <p className="text-xs text-slate-500">Retrieved sources for the latest answer will appear here.</p>
      </aside>
    );
  }

  const cited = citedIndices === null ? [] : sources.filter((s) => citedIndices.includes(s.index));
  const uncited = citedIndices === null ? sources : sources.filter((s) => !citedIndices.includes(s.index));

  return (
    <aside className="w-80 shrink-0 overflow-y-auto border-l border-slate-800 bg-slate-900/40 p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-slate-500">
        {isStreaming ? "Searching…" : "Sources"}
      </p>

      {citedIndices !== null && cited.length > 0 && (
        <div className="mb-5">
          <p className="mb-2 text-xs font-medium text-sky-400">Cited in answer</p>
          <div className="space-y-2">
            {cited.map((s) => (
              <SourceCard key={s.index} source={s} cited />
            ))}
          </div>
        </div>
      )}

      <div>
        <p className="mb-2 text-xs font-medium text-slate-500">
          {citedIndices === null ? "Searched" : "Also searched, not cited"}
        </p>
        {uncited.length === 0 && citedIndices !== null && (
          <p className="text-xs text-slate-600">Every retrieved source was cited.</p>
        )}
        <div className="space-y-2">
          {uncited.map((s) => (
            <SourceCard key={s.index} source={s} cited={false} />
          ))}
        </div>
      </div>
    </aside>
  );
}

function SourceCard({ source, cited }: { source: Source; cited: boolean }) {
  return (
    <a
      href={source.url}
      target="_blank"
      rel="noreferrer"
      className={`block rounded-md border p-2.5 text-xs transition hover:border-slate-600 ${
        cited ? "border-sky-500/30 bg-sky-500/5" : "border-slate-800 bg-slate-900/60"
      }`}
    >
      <div className="mb-1 flex items-center gap-1.5">
        <span
          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded text-[10px] font-semibold ${
            cited ? "bg-sky-500/30 text-sky-200" : "bg-slate-700 text-slate-300"
          }`}
        >
          {source.index}
        </span>
        <SourceIcon source={source.source} className="h-3.5 w-3.5 shrink-0 text-slate-400" />
        <span className="truncate font-medium text-slate-200">{source.title}</span>
      </div>
      <p className="line-clamp-3 text-slate-500">{source.snippet}</p>
    </a>
  );
}
