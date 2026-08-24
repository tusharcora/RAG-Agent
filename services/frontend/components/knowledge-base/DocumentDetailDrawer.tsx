"use client";

import { useEffect, useState } from "react";
import { X } from "@untitledui/icons";
import { getDocument } from "@/lib/api";
import { Button } from "@/components/base/buttons/button";
import { Badge } from "@/components/base/badges/badges";
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
            <Button color="link-color" size="sm" href={detail.url} target="_blank" rel="noreferrer">
              View original
            </Button>
          )}
        </div>
        <Button color="tertiary" size="sm" iconLeading={X} onPress={onClose} className="shrink-0 rounded-full" />
      </div>

      {error && <p className="text-sm text-coral-400">Couldn't load this document.</p>}

      {detail && (
        <div className="space-y-3">
          <p className="text-xs text-ink-500">
            {detail.chunks.length} chunk{detail.chunks.length === 1 ? "" : "s"} — this is what retrieval sees for
            this document.
          </p>
          {detail.chunks.map((chunk) => (
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
