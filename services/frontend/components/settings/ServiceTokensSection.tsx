"use client";

import { useEffect, useState } from "react";
import { Copy01, Plus } from "@untitledui/icons";
import { createServiceToken, listServiceTokens, revokeServiceToken } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Badge } from "@/components/base/badges/badges";
import { Table, TableCard } from "@/components/application/table/table";
import { Modal } from "@/components/base/modal/modal";
import { ConfirmDialog } from "@/components/base/modal/confirm-dialog";
import { TableRowsSkeleton } from "@/components/base/skeleton/skeleton";
import type { ServiceTokenSummary } from "@/lib/types";

// Owner/admin only, matching require_role(auth, "owner", "admin") on all
// three /auth/service-tokens routes.
export function ServiceTokensSection() {
  const [tokens, setTokens] = useState<ServiceTokenSummary[] | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ServiceTokenSummary | null>(null);
  const [revoking, setRevoking] = useState(false);
  const copy = useCopyToClipboard();

  const refresh = () => {
    listServiceTokens()
      .then(setTokens)
      .catch(() => setTokens([]));
  };

  useEffect(refresh, []);

  async function handleCreate() {
    if (!label.trim()) return;
    setCreating(true);
    try {
      const result = await createServiceToken(label.trim());
      setCreatedToken(result.token);
      setLabel("");
      refresh();
    } catch {
      toast.error("Couldn't create token", "Please try again.");
    } finally {
      setCreating(false);
    }
  }

  function closeCreateModal() {
    setShowCreate(false);
    setCreatedToken(null);
  }

  async function handleRevoke() {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await revokeServiceToken(revokeTarget.id);
      toast.success("Token revoked");
      setRevokeTarget(null);
      refresh();
    } catch {
      toast.error("Couldn't revoke token", "Please try again.");
    } finally {
      setRevoking(false);
    }
  }

  return (
    <TableCard.Root>
      <TableCard.Header
        title="Service tokens"
        description="Org-scoped credentials for non-interactive automation, e.g. triggering a sync from a script."
        contentTrailing={
          <Button color="secondary" size="sm" iconLeading={Plus} onPress={() => setShowCreate(true)}>
            Create token
          </Button>
        }
      />
      {tokens === null ? (
        <TableRowsSkeleton columns={3} rows={2} />
      ) : tokens.length === 0 ? (
        <p className="px-1 py-8 text-center text-sm text-ink-500">No service tokens yet.</p>
      ) : (
        <Table aria-label="Service tokens" size="sm">
          <Table.Header>
            <Table.Head id="label" label="Label" isRowHeader className="w-full" />
            <Table.Head id="created" label="Created" />
            <Table.Head id="status" label="Status" />
            <Table.Head id="action" label="" />
          </Table.Header>
          <Table.Body items={tokens}>
            {(t) => (
              <Table.Row id={t.id}>
                <Table.Cell className="font-medium text-secondary">{t.label}</Table.Cell>
                <Table.Cell className="whitespace-nowrap">{new Date(t.created_at).toLocaleDateString()}</Table.Cell>
                <Table.Cell>
                  {t.revoked_at ? (
                    <Badge type="pill-color" color="gray" size="sm">
                      Revoked
                    </Badge>
                  ) : (
                    <Badge type="pill-color" color="success" size="sm">
                      Active
                    </Badge>
                  )}
                </Table.Cell>
                <Table.Cell className="text-right">
                  {!t.revoked_at && (
                    <Button color="secondary" size="sm" onPress={() => setRevokeTarget(t)}>
                      Revoke
                    </Button>
                  )}
                </Table.Cell>
              </Table.Row>
            )}
          </Table.Body>
        </Table>
      )}

      <Modal
        isOpen={showCreate}
        onOpenChange={(open) => !open && closeCreateModal()}
        aria-label={createdToken ? "Service token created" : "Create service token"}
      >
        {createdToken ? (
          <>
            <p className="text-sm font-semibold text-ink-100">Token created</p>
            <p className="mt-1.5 text-sm text-ink-400">Copy this now — you will not be able to see it again.</p>
            <div className="mt-3 flex items-center gap-2 rounded-lg border border-ink-800 bg-ink-950/60 px-3 py-2">
              <code className="flex-1 truncate font-mono text-xs text-ink-200">{createdToken}</code>
              <button
                type="button"
                aria-label="Copy service token"
                onClick={() => copy(createdToken, "Token copied")}
                className="shrink-0 rounded-md p-1 text-ink-500 transition hover:text-ink-200"
              >
                <Copy01 className="h-4 w-4" />
              </button>
            </div>
            <div className="mt-4 flex justify-end">
              <Button color="secondary" size="sm" onPress={closeCreateModal}>
                Done
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm font-semibold text-ink-100">Create service token</p>
            <Input size="sm" placeholder="e.g. CI sync trigger" value={label} onChange={setLabel} className="mt-3" />
            <div className="mt-4 flex justify-end gap-2">
              <Button color="secondary" size="sm" onPress={() => setShowCreate(false)}>
                Cancel
              </Button>
              <Button color="primary" size="sm" isLoading={creating} isDisabled={!label.trim()} onPress={handleCreate}>
                Create
              </Button>
            </div>
          </>
        )}
      </Modal>

      <ConfirmDialog
        isOpen={revokeTarget !== null}
        onOpenChange={(open) => !open && setRevokeTarget(null)}
        title="Revoke this token?"
        description={`${revokeTarget?.label ?? ""} will stop working immediately — this can't be undone.`}
        confirmLabel="Revoke"
        isDestructive
        isLoading={revoking}
        onConfirm={handleRevoke}
      />
    </TableCard.Root>
  );
}
