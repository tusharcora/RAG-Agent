"use client";

import { useCallback, useEffect, useState } from "react";
import { SearchLg } from "@untitledui/icons";
import { getDocuments } from "@/lib/api";
import { DocumentTable } from "@/components/knowledge-base/DocumentTable";
import { DocumentDetailDrawer } from "@/components/knowledge-base/DocumentDetailDrawer";
import { StatTile } from "@/components/StatTile";
import { TableCard } from "@/components/application/table/table";
import { Input } from "@/components/base/input/input";
import { NativeSelect } from "@/components/base/select/select-native";
import { Button } from "@/components/base/buttons/button";
import type { DocumentSummary } from "@/lib/types";

const PAGE_SIZE = 25;

const SOURCE_OPTIONS = [
  { label: "All sources", value: "" },
  { label: "Notion", value: "notion" },
  { label: "Jira", value: "jira" },
];

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

        <TableCard.Root className="flex min-h-0 flex-1 flex-col">
          <TableCard.Header
            title="Documents"
            description="Click a row to inspect its chunks."
            contentTrailing={
              <div className="flex gap-2">
                <Input
                  icon={SearchLg}
                  size="sm"
                  placeholder="Search titles..."
                  value={search}
                  onChange={(v) => {
                    setOffset(0);
                    setSearch(v);
                  }}
                  wrapperClassName="w-56"
                />
                <NativeSelect
                  size="sm"
                  options={SOURCE_OPTIONS}
                  value={source}
                  onChange={(e) => {
                    setOffset(0);
                    setSource(e.target.value);
                  }}
                  className="w-36"
                />
              </div>
            }
          />
          <div className="flex-1 overflow-y-auto">
            <DocumentTable documents={documents} selectedId={selectedId} onSelect={setSelectedId} />
          </div>
          <div className="flex items-center justify-between border-t border-secondary px-4 py-3 text-xs text-ink-500 md:px-6">
            <span>
              {total === 0 ? "0" : `${offset + 1}-${Math.min(offset + PAGE_SIZE, total)}`} of {total}
            </span>
            <div className="flex gap-2">
              <Button
                color="secondary"
                size="sm"
                isDisabled={offset === 0}
                onPress={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                color="secondary"
                size="sm"
                isDisabled={offset + PAGE_SIZE >= total}
                onPress={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </TableCard.Root>
      </div>

      {selectedId && (
        <DocumentDetailDrawer
          documentId={selectedId}
          onClose={() => setSelectedId(null)}
          onExcludedChange={(excluded) =>
            setDocuments((prev) => prev.map((d) => (d.id === selectedId ? { ...d, excluded_from_retrieval: excluded } : d)))
          }
        />
      )}
    </div>
  );
}
