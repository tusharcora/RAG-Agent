import type { Selection } from "react-aria-components";
import { SourceIcon } from "@/components/icons/SourceIcon";
import { Table } from "@/components/application/table/table";
import { Badge } from "@/components/base/badges/badges";
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

  const handleSelectionChange = (keys: Selection) => {
    if (keys === "all") return;
    const [id] = Array.from(keys);
    if (id) onSelect(String(id));
  };

  return (
    <Table
      aria-label="Synced documents"
      selectionMode="single"
      selectionBehavior="replace"
      selectedKeys={selectedId ? new Set([selectedId]) : new Set()}
      onSelectionChange={handleSelectionChange}
    >
      <Table.Header>
        <Table.Head id="title" label="Title" isRowHeader className="w-full" />
        <Table.Head id="source" label="Source" />
        <Table.Head id="synced" label="Last synced" />
        <Table.Head id="chunks" label="Chunks" />
      </Table.Header>
      <Table.Body items={documents}>
        {(doc) => (
          <Table.Row id={doc.id}>
            <Table.Cell className="max-w-xs truncate font-medium text-secondary">
              <span className="flex items-center gap-1.5">
                <span className="truncate">{doc.title}</span>
                {doc.excluded_from_retrieval && (
                  <Badge type="pill-color" color="warning" size="sm" className="shrink-0">
                    Excluded
                  </Badge>
                )}
              </span>
            </Table.Cell>
            <Table.Cell>
              <span className="flex items-center gap-1.5 whitespace-nowrap">
                <SourceIcon source={doc.source} className="h-3.5 w-3.5" />
                {doc.source}
              </span>
            </Table.Cell>
            <Table.Cell className="whitespace-nowrap">{new Date(doc.synced_at).toLocaleString()}</Table.Cell>
            <Table.Cell>
              <Badge type="color" color="gray" size="sm">
                {doc.chunk_count}
              </Badge>
            </Table.Cell>
          </Table.Row>
        )}
      </Table.Body>
    </Table>
  );
}
