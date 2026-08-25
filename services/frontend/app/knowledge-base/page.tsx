"use client";

import { useCallback, useEffect, useState } from "react";
import { SearchLg } from "@untitledui/icons";
import { getDocuments, setDocumentExcluded } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { toast } from "@/lib/toast";
import { DocumentTable } from "@/components/knowledge-base/DocumentTable";
import { DocumentDetailDrawer } from "@/components/knowledge-base/DocumentDetailDrawer";
import { StatTile } from "@/components/StatTile";
import { TableCard } from "@/components/application/table/table";
import { Input } from "@/components/base/input/input";
import { NativeSelect } from "@/components/base/select/select-native";
import { Button } from "@/components/base/buttons/button";
import { TableRowsSkeleton } from "@/components/base/skeleton/skeleton";
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
  // Debounced separately from `search` so typing doesn't fire a request per
  // keystroke — same 300ms pattern as SessionSidebar.tsx's session search.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [source, setSource] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  // Checkbox selection for bulk actions, independent of openId (which document
  // is showing in the detail drawer).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkExcluding, setBulkExcluding] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(id);
  }, [search]);

  const refresh = useCallback(() => {
    getDocuments({ search: debouncedSearch || undefined, source: source || undefined, limit: PAGE_SIZE, offset })
      .then((res) => {
        setDocuments(res.items);
        setTotal(res.total);
        setLoaded(true);
        // Selection can't outlive the page it was made on — drop anything
        // that scrolled out of view after a refetch.
        setSelectedIds((prev) => new Set([...prev].filter((id) => res.items.some((d) => d.id === id))));
      })
      .catch(() => {});
  }, [debouncedSearch, source, offset]);

  useEffect(refresh, [refresh]);

  async function handleBulkExclude() {
    setBulkExcluding(true);
    const ids = Array.from(selectedIds);
    const results = await Promise.allSettled(ids.map((id) => setDocumentExcluded(id, true)));
    const succeeded = new Set(ids.filter((_, i) => results[i].status === "fulfilled"));
    setDocuments((prev) => prev.map((d) => (succeeded.has(d.id) ? { ...d, excluded_from_retrieval: true } : d)));
    setSelectedIds(new Set());
    setBulkExcluding(false);
    if (succeeded.size === ids.length) {
      toast.success(`Excluded ${succeeded.size} document${succeeded.size === 1 ? "" : "s"}`);
    } else {
      toast.error(`Excluded ${succeeded.size} of ${ids.length}`, "Some documents couldn't be updated — try again.");
    }
  }

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
          {isAdmin && selectedIds.size > 0 && (
            <div className="flex items-center justify-between border-b border-secondary bg-secondary/40 px-4 py-2 md:px-6">
              <span className="text-xs text-ink-400">{selectedIds.size} selected</span>
              <Button color="secondary" size="sm" isLoading={bulkExcluding} onPress={handleBulkExclude}>
                Exclude from retrieval
              </Button>
            </div>
          )}
          <div className="flex-1 overflow-y-auto">
            {loaded ? (
              <DocumentTable documents={documents} selectedIds={selectedIds} onSelectionChange={setSelectedIds} onOpen={setOpenId} />
            ) : (
              <TableRowsSkeleton columns={4} />
            )}
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

      <DocumentDetailDrawer
        isOpen={openId !== null}
        documentId={openId}
        onClose={() => setOpenId(null)}
        onExcludedChange={(excluded) =>
          setDocuments((prev) => prev.map((d) => (d.id === openId ? { ...d, excluded_from_retrieval: excluded } : d)))
        }
      />
    </div>
  );
}
