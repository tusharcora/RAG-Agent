"use client";

import { useEffect, useState } from "react";
import { getDocument } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";

export function DocumentDetailDrawer({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setDetail(null);
    setError(false);
    getDocument(documentId)
      .then(setDetail)
      .catch(() => setError(true));
  }, [documentId]);

  return (
    <aside className="w-[28rem] shrink-0 overflow-y-auto border-l border-ink-800 bg-ink-900/40 p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink-100">{detail?.title ?? "Loading…"}</p>
          {detail && (
            <a href={detail.url} target="_blank" rel="noreferrer" className="text-xs text-coral-400 hover:underline">
              View original
            </a>
          )}
        </div>
        <button type="button" onClick={onClose} className="shrink-0 text-ink-500 hover:text-ink-300">
          ✕
        </button>
      </div>

      {error && <p className="text-sm text-coral-400">Couldn't load this document.</p>}

      {detail && (
        <div className="space-y-3">
          <p className="text-xs text-ink-500">
            {detail.chunks.length} chunk{detail.chunks.length === 1 ? "" : "s"} — this is what retrieval sees for
            this document.
          </p>
          {detail.chunks.map((chunk) => (
            <div key={chunk.id} className="rounded-xl border border-ink-800 bg-ink-950/60 p-3">
              <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-500">
                <span className="rounded bg-ink-800 px-1.5 py-0.5 font-mono">#{chunk.chunk_index}</span>
                {chunk.token_count != null && <span>{chunk.token_count} tok</span>}
                {Object.entries(chunk.metadata).map(([key, value]) => (
                  <span key={key} className="rounded bg-ink-800 px-1.5 py-0.5">
                    {key}: {Array.isArray(value) ? value.join(" › ") : String(value)}
                  </span>
                ))}
              </div>
              <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap font-mono text-xs text-ink-300">
                {chunk.content}
              </pre>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
