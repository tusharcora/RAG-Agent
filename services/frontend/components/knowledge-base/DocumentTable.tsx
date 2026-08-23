import { SourceIcon } from "@/components/icons/SourceIcon";
import type { DocumentSummary } from "@/lib/types";

export function DocumentTable({
  documents,
  selectedId,
  onSelect,
}: {
  documents: DocumentSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (documents.length === 0) {
    return <p className="px-1 py-8 text-center text-sm text-ink-500">No documents match this filter.</p>;
  }

  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b border-ink-800 text-xs uppercase tracking-wide text-ink-500">
          <th className="py-2 pr-3 font-medium">Title</th>
          <th className="py-2 pr-3 font-medium">Source</th>
          <th className="py-2 pr-3 font-medium">Last synced</th>
          <th className="py-2 pr-3 font-medium">Chunks</th>
        </tr>
      </thead>
      <tbody>
        {documents.map((doc) => (
          <tr
            key={doc.id}
            onClick={() => onSelect(doc.id)}
            className={`cursor-pointer border-b border-ink-900 transition hover:bg-ink-800/40 ${
              selectedId === doc.id ? "bg-coral-500/10" : ""
            }`}
          >
            <td className="max-w-xs truncate py-2.5 pr-3 text-ink-200">{doc.title}</td>
            <td className="py-2.5 pr-3">
              <span className="flex items-center gap-1.5 text-ink-400">
                <SourceIcon source={doc.source} className="h-3.5 w-3.5" />
                {doc.source}
              </span>
            </td>
            <td className="py-2.5 pr-3 text-ink-400">{new Date(doc.synced_at).toLocaleString()}</td>
            <td className="py-2.5 pr-3">
              <span className="rounded-full bg-ink-800 px-2 py-0.5 text-xs text-ink-300">{doc.chunk_count}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
