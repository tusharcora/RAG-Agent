import { useEffect, useRef } from "react";
import { SourceIcon } from "@/components/icons/SourceIcon";
import { providerLabel } from "@/lib/format";
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
  // Set when a [n] citation chip in an answer is clicked: scrolls the matching
  // card into view and focuses it.
  highlightedIndex?: number | null;
}) {
  const cardRefs = useRef(new Map<number, HTMLAnchorElement>());

  useEffect(() => {
    if (highlightedIndex == null) return;
    cardRefs.current.get(highlightedIndex)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [highlightedIndex]);

  const shell =
    "hidden w-[380px] shrink-0 flex-col gap-[18px] overflow-y-auto py-7 pr-7 pl-1 xl:flex";

  if (sources.length === 0) {
    return (
      <aside className={shell} aria-label="Sources">
        <span className="font-mono text-[11px] tracking-[0.08em] text-ink-500">SOURCES</span>
        <p className="text-[13px] leading-relaxed text-ink-500">
          {isStreaming ? "Searching your workspace…" : "The documents behind each answer show up here, with what was cited and what was only searched."}
        </p>
      </aside>
    );
  }

  const cited = citedIndices === null ? [] : sources.filter((s) => citedIndices.includes(s.index));
  const uncited = citedIndices === null ? sources : sources.filter((s) => !citedIndices.includes(s.index));
  const focus = highlightedIndex ?? cited[0]?.index ?? null;

  return (
    <aside className={shell} aria-label="Sources">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-[11px] tracking-[0.08em] text-ink-500">
          {isStreaming || citedIndices === null
            ? `SEARCHED · ${sources.length}`
            : `CITED · ${cited.length} OF ${sources.length}`}
        </span>
        {cited.length > 0 && <span className="text-xs text-ink-500">click a chip to focus</span>}
      </div>

      {cited.map((s) => (
        <SourceCard
          key={s.index}
          source={s}
          focused={focus === s.index}
          registerRef={(el) => {
            if (el) cardRefs.current.set(s.index, el);
          }}
        />
      ))}

      {uncited.length > 0 && (
        <div className="mt-1.5 flex flex-col gap-0.5">
          <p className="pb-2 font-mono text-[11px] tracking-[0.08em] text-ink-500">
            {citedIndices === null ? "RETRIEVED" : `SEARCHED, NOT CITED · ${uncited.length}`}
          </p>
          {uncited.map((s) => (
            <a
              key={s.index}
              ref={(el) => {
                if (el) cardRefs.current.set(s.index, el);
              }}
              href={s.url}
              target="_blank"
              rel="noreferrer"
              className={`flex items-center justify-between gap-3 rounded-[10px] px-2.5 py-2 text-[13px] text-ink-400 transition hover:bg-ink-900 hover:text-ink-100 ${
                highlightedIndex === s.index ? "bg-ink-900 text-ink-100" : ""
              }`}
            >
              <span className="truncate">{s.title}</span>
              <span className="shrink-0 font-mono text-[11px] text-ink-500">{providerLabel(s.source)}</span>
            </a>
          ))}
        </div>
      )}
    </aside>
  );
}

function SourceCard({
  source,
  focused,
  registerRef,
}: {
  source: Source;
  focused: boolean;
  registerRef?: (el: HTMLAnchorElement | null) => void;
}) {
  return (
    <a
      ref={registerRef}
      href={source.url}
      target="_blank"
      rel="noreferrer"
      className={`flex flex-col gap-3 rounded-2xl border px-[18px] pt-[18px] pb-4 transition duration-200 ${
        focused
          ? "-translate-y-0.5 border-ink-100 bg-ink-900 shadow-[0_18px_36px_-22px_rgb(0_0_0/0.45)]"
          : "border-ink-800 bg-ink-850 opacity-80 hover:opacity-100"
      }`}
    >
      <div className="flex items-center gap-2.5">
        <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-ink-100 font-mono text-[11px] text-ink-950">
          {source.index}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium leading-snug text-ink-100">{source.title}</span>
        <span className="flex shrink-0 items-center gap-1 font-mono text-[11px] text-ink-400">
          <SourceIcon source={source.source} className="h-3 w-3" />
          {providerLabel(source.source)}
        </span>
      </div>
      <p className="line-clamp-4 font-serif text-[19px] leading-[1.35] text-ink-200">{source.snippet}</p>
    </a>
  );
}
