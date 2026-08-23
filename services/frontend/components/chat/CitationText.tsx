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
              className="mx-0.5 inline-flex h-4 min-w-[1rem] items-center justify-center rounded bg-gold-400/20 px-1 align-text-top text-[11px] font-medium text-gold-300 hover:bg-gold-400/40"
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
