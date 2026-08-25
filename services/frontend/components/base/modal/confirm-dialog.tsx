"use client";

import { Button } from "@/components/base/buttons/button";
import { Modal } from "@/components/base/modal/modal";

export interface ConfirmDialogProps {
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Uses the destructive button color and "This can't be undone" styling. */
  isDestructive?: boolean;
  isLoading?: boolean;
  onConfirm: () => void;
}

/**
 * The delete-session confirm pattern from components/chat/SessionSidebar.tsx,
 * generalized so other destructive actions (disconnect, revoke token, redrive)
 * don't have to hand-roll the same ModalOverlay/Modal/Dialog markup again.
 */
export function ConfirmDialog({
  isOpen,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  isDestructive,
  isLoading,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Modal isOpen={isOpen} onOpenChange={onOpenChange} className="w-80" aria-label={title}>
      <p className="text-sm font-semibold text-ink-100">{title}</p>
      {description && <p className="mt-1.5 text-sm text-ink-400">{description}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button color="secondary" size="sm" onPress={() => onOpenChange(false)} isDisabled={isLoading}>
          {cancelLabel}
        </Button>
        <Button color={isDestructive ? "primary-destructive" : "primary"} size="sm" onPress={onConfirm} isLoading={isLoading}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
