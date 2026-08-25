import { useEffect, useRef } from "react";
import { SourceIcon } from "@/components/icons/SourceIcon";
import type { Source } from "@/lib/types";

export function SourcesPanel({
  sources,
  citedIndices,
  isStreaming,
  highlightedIndex,
}: {
  sources: Source[];
  citedIndices: number[] | null;
  isStreaming: boolean;
  // Set by app/page.tsx when a [n] citation marker in an answer is clicked —
  // scrolls the matching source card into view and flashes it, so the
  // citation markers (previously wired to nothing) actually do something.
  highlightedIndex?: number | null;
}) {
  const cardRefs = useRef(new Map<number, HTMLAnchorElement>());

  useEffect(() => {
    if (highlightedIndex == null) return;
    cardRefs.current.get(highlightedIndex)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [highlightedIndex]);

  if (sources.length === 0) {
    return (
      <aside className="w-80 shrink-0 border-l border-ink-800 bg-ink-900/40 p-4">
        <p className="text-xs text-ink-500">Retrieved sources for the latest answer will appear here.</p>
      </aside>
    );
  }

  const cited = citedIndices === null ? [] : sources.filter((s) => citedIndices.includes(s.index));
  const uncited = citedIndices === null ? sources : sources.filter((s) => !citedIndices.includes(s.index));

  return (
    <aside className="w-80 shrink-0 overflow-y-auto border-l border-ink-800 bg-ink-900/40 p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-ink-500">
        {isStreaming ? "Searching…" : "Sources"}
      </p>

      {citedIndices !== null && cited.length > 0 && (
        <div className="mb-5">
          <p className="mb-2 text-xs font-medium text-gold-400">Cited in answer</p>
          <div className="space-y-2">
            {cited.map((s) => (
              <SourceCard
                key={s.index}
                source={s}
                cited
                highlighted={highlightedIndex === s.index}
                registerRef={(el) => {
                  if (el) cardRefs.current.set(s.index, el);
                }}
              />
            ))}
          </div>
        </div>
      )}

      <div>
        <p className="mb-2 text-xs font-medium text-ink-500">
          {citedIndices === null ? "Searched" : "Also searched, not cited"}
        </p>
        {uncited.length === 0 && citedIndices !== null && (
          <p className="text-xs text-ink-600">Every retrieved source was cited.</p>
        )}
        <div className="space-y-2">
          {uncited.map((s) => (
            <SourceCard
              key={s.index}
              source={s}
              cited={false}
              highlighted={highlightedIndex === s.index}
              registerRef={(el) => {
                if (el) cardRefs.current.set(s.index, el);
              }}
            />
          ))}
        </div>
      </div>
    </aside>
  );
}

function SourceCard({
  source,
  cited,
  highlighted,
  registerRef,
}: {
  source: Source;
  cited: boolean;
  highlighted?: boolean;
  registerRef?: (el: HTMLAnchorElement | null) => void;
}) {
  return (
    <a
      ref={registerRef}
      href={source.url}
      target="_blank"
      rel="noreferrer"
      className={`block rounded-xl border p-2.5 text-xs transition hover:border-ink-600 ${
        cited ? "border-gold-400/30 bg-gold-400/5" : "border-ink-800 bg-ink-900/60"
      } ${highlighted ? "ring-2 ring-gold-400" : ""}`}
    >
      <div className="mb-1 flex items-center gap-1.5">
        <span
          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded text-[10px] font-semibold ${
            cited ? "bg-gold-400/25 text-gold-300" : "bg-ink-700 text-ink-300"
          }`}
        >
          {source.index}
        </span>
        <SourceIcon source={source.source} className="h-3.5 w-3.5 shrink-0 text-ink-400" />
        <span className="truncate font-medium text-ink-200">{source.title}</span>
      </div>
      <p className="line-clamp-3 text-ink-500">{source.snippet}</p>
    </a>
  );
}
