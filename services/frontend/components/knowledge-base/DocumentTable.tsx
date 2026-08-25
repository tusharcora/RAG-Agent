import type { Selection } from "react-aria-components";
import { FileSearch02 } from "@untitledui/icons";
import { SourceIcon } from "@/components/icons/SourceIcon";
import { Table } from "@/components/application/table/table";
import { Badge } from "@/components/base/badges/badges";
import { EmptyState } from "@/components/base/empty-state/empty-state";
import type { DocumentSummary } from "@/lib/types";

export function DocumentTable({
  documents,
  selectedIds,
  onSelectionChange,
  onOpen,
}: {
  documents: DocumentSummary[];
  // Checkbox selection for bulk actions — independent of which document is
  // open in the detail drawer (see onOpen), which used to be the same state.
  selectedIds: Set<string>;
  onSelectionChange: (ids: Set<string>) => void;
  onOpen: (id: string) => void;
}) {
  if (documents.length === 0) {
    return (
      <EmptyState
        icon={FileSearch02}
        title="No documents match this filter"
        description="Try a different search, or connect a source from the Connections page to start syncing content."
      />
    );
  }

  const handleSelectionChange = (keys: Selection) => {
    if (keys === "all") {
      onSelectionChange(new Set(documents.map((d) => d.id)));
      return;
    }
    onSelectionChange(new Set(Array.from(keys, String)));
  };

  return (
    <Table
      aria-label="Synced documents"
      selectionMode="multiple"
      selectedKeys={selectedIds}
      onSelectionChange={handleSelectionChange}
      onRowAction={(key) => onOpen(String(key))}
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
