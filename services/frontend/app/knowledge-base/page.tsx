"use client";

import { useCallback, useEffect, useState } from "react";
import { getDocuments } from "@/lib/api";
import { DocumentTable } from "@/components/knowledge-base/DocumentTable";
import { DocumentDetailDrawer } from "@/components/knowledge-base/DocumentDetailDrawer";
import { StatTile } from "@/components/StatTile";
import type { DocumentSummary } from "@/lib/types";

const PAGE_SIZE = 25;

export default function KnowledgeBasePage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [source, setSource] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getDocuments({ search: search || undefined, source: source || undefined, limit: PAGE_SIZE, offset })
      .then((res) => {
        setDocuments(res.items);
        setTotal(res.total);
      })
      .catch(() => {});
  }, [search, source, offset]);

  useEffect(refresh, [refresh]);

  return (
    <div className="mx-auto flex h-[calc(100vh-57px)] max-w-none">
      <div className="flex min-w-0 flex-1 flex-col px-6 py-6">
        <h1 className="mb-1 text-xl font-semibold text-ink-100">Knowledge Base</h1>
        <p className="mb-4 text-sm text-ink-500">Everything ingested from Notion and Jira, and what it chunks into.</p>

        <div className="mb-4 flex items-center gap-3 text-xs text-ink-500">
          <StatTile label="Documents" value={total} />
        </div>

        <div className="mb-4 flex gap-2">
          <input
            value={search}
            onChange={(e) => {
              setOffset(0);
              setSearch(e.target.value);
            }}
            placeholder="Search titles…"
            className="w-64 rounded-xl border border-ink-700 bg-ink-950 px-3 py-1.5 text-sm text-ink-100 placeholder:text-ink-600 focus:border-coral-500 focus:outline-none"
          />
          <select
            value={source}
            onChange={(e) => {
              setOffset(0);
              setSource(e.target.value);
            }}
            className="rounded-xl border border-ink-700 bg-ink-950 px-3 py-1.5 text-sm text-ink-100 focus:border-coral-500 focus:outline-none"
          >
            <option value="">All sources</option>
            <option value="notion">Notion</option>
            <option value="jira">Jira</option>
          </select>
        </div>

        <div className="flex-1 overflow-y-auto rounded-2xl border border-ink-800 bg-ink-900/40 p-2 shadow-panel">
          <DocumentTable documents={documents} selectedId={selectedId} onSelect={setSelectedId} />
        </div>

        <div className="mt-3 flex items-center justify-between text-xs text-ink-500">
          <span>
            {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {total}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="rounded-full border border-ink-700 px-3 py-1 transition hover:border-ink-600 disabled:opacity-40"
            >
              Prev
            </button>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="rounded-full border border-ink-700 px-3 py-1 transition hover:border-ink-600 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      </div>

      {selectedId && <DocumentDetailDrawer documentId={selectedId} onClose={() => setSelectedId(null)} />}
    </div>
  );
}
