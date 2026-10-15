export function CitationText({ text, onCitationClick }: { text: string; onCitationClick?: (index: number) => void }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const match = part.match(/^\[(\d+)\]$/);
        if (match) {
          const index = Number(match[1]);
          return (
            <button
              key={i}
              type="button"
              onClick={() => onCitationClick?.(index)}
              className="mx-0.5 inline-flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-accent px-1 align-text-top font-mono text-[11px] font-medium text-accent-ink transition hover:opacity-80"
            >
              {index}
            </button>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}
