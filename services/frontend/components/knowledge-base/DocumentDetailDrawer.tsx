"use client";

import { useEffect, useState } from "react";
import { Copy01, X } from "@untitledui/icons";
import { getDocument, setDocumentExcluded } from "@/lib/api";
import { Button } from "@/components/base/buttons/button";
import { Badge } from "@/components/base/badges/badges";
import { Toggle } from "@/components/base/toggle/toggle";
import { ErrorState } from "@/components/base/error-state/error-state";
import { DocumentDrawerSkeleton } from "@/components/base/skeleton/skeleton";
import { useAuth } from "@/lib/auth-context";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import type { DocumentDetail } from "@/lib/types";

export function DocumentDetailDrawer({
  documentId,
  onClose,
  onExcludedChange,
}: {
  documentId: string;
  onClose: () => void;
  onExcludedChange?: (excluded: boolean) => void;
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState(false);
  const [savingExcluded, setSavingExcluded] = useState(false);
  const [expandedChunks, setExpandedChunks] = useState<Set<string>>(new Set());
  const { user } = useAuth();
  const copy = useCopyToClipboard();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  const load = () => {
    setDetail(null);
    setError(false);
    getDocument(documentId)
      .then(setDetail)
      .catch(() => setError(true));
  };

  useEffect(() => {
    load();
    setExpandedChunks(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  async function handleExcludedChange(excluded: boolean) {
    if (!detail) return;
    setSavingExcluded(true);
    try {
      await setDocumentExcluded(documentId, excluded);
      setDetail({ ...detail, excluded_from_retrieval: excluded });
      onExcludedChange?.(excluded);
    } finally {
      setSavingExcluded(false);
    }
  }

  function toggleExpanded(chunkId: string) {
    setExpandedChunks((prev) => {
      const next = new Set(prev);
      if (next.has(chunkId)) next.delete(chunkId);
      else next.add(chunkId);
      return next;
    });
  }

  return (
    <aside className="w-[28rem] shrink-0 overflow-y-auto border-l border-ink-800 bg-ink-900/40 p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink-100">{detail?.title ?? (error ? "Couldn't load" : "Loading…")}</p>
          {detail && (
            <Button color="link-color" size="sm" href={detail.url} target="_blank" rel="noreferrer">
              View original
            </Button>
          )}
        </div>
        <Button color="tertiary" size="sm" iconLeading={X} onPress={onClose} className="shrink-0 rounded-full" />
      </div>

      {error && <ErrorState title="Couldn't load this document" onRetry={load} />}
      {!detail && !error && <DocumentDrawerSkeleton />}

      {detail && isAdmin && (
        <div className="mb-3 flex items-center justify-between gap-2 rounded-xl border border-ink-800 bg-ink-950/60 p-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-ink-200">Exclude from retrieval</p>
            <p className="text-xs text-ink-500">Stop citing this document in /query without disconnecting the sync.</p>
          </div>
          <Toggle
            isSelected={detail.excluded_from_retrieval}
            isDisabled={savingExcluded}
            onChange={handleExcludedChange}
            aria-label="Exclude from retrieval"
          />
        </div>
      )}

      {detail && (
        <div className="space-y-3">
          <p className="text-xs text-ink-500">
            {detail.chunks.length} chunk{detail.chunks.length === 1 ? "" : "s"} — this is what retrieval sees for
            this document.
          </p>
          {detail.chunks.map((chunk) => {
            const isExpanded = expandedChunks.has(chunk.id);
            return (
              <div key={chunk.id} className="cyber-chamfer-sm rounded-xl border border-ink-800 bg-ink-950/60 p-3">
                <div className="mb-2 flex flex-wrap items-center gap-1.5">
                  <Badge type="color" color="gray" size="sm" className="font-mono">
                    #{chunk.chunk_index}
                  </Badge>
                  {chunk.token_count != null && (
                    <Badge type="color" color="gray" size="sm">
                      {chunk.token_count} tok
                    </Badge>
                  )}
                  {Object.entries(chunk.metadata).map(([key, value]) => (
                    <Badge key={key} type="color" color="gray" size="sm">
                      {key}: {Array.isArray(value) ? value.join(" › ") : String(value)}
                    </Badge>
                  ))}
                  <button
                    type="button"
                    aria-label="Copy chunk content"
                    onClick={() => copy(chunk.content, "Chunk copied")}
                    className="ml-auto rounded-md p-1 text-ink-600 transition hover:text-ink-300"
                  >
                    <Copy01 className="h-3.5 w-3.5" />
                  </button>
                </div>
                <pre
                  className={`overflow-y-auto whitespace-pre-wrap font-mono text-xs text-ink-300 ${isExpanded ? "max-h-none" : "max-h-40"}`}
                >
                  {chunk.content}
                </pre>
                <button
                  type="button"
                  onClick={() => toggleExpanded(chunk.id)}
                  className="mt-1 text-xs text-coral-400 hover:text-coral-300"
                >
                  {isExpanded ? "Show less" : "Show more"}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </aside>
  );
}
