"use client";

import type { ReactNode } from "react";
import { Dialog, Modal as AriaModal, ModalOverlay } from "react-aria-components";
import { cx } from "@/utils/cx";

export interface ModalProps {
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  /** "center" (default) is a centered dialog card; "drawer" slides in from the right edge. */
  variant?: "center" | "drawer";
  isDismissable?: boolean;
  className?: string;
  "aria-label"?: string;
  children: ReactNode;
}

/**
 * Generic overlay + panel, extracted from the delete-session confirm dialog
 * originally built inline in components/chat/SessionSidebar.tsx. Focus trap,
 * outside-click dismiss, and Escape-to-close all come from react-aria-components
 * for free.
 */
export function Modal({ isOpen, onOpenChange, variant = "center", isDismissable = true, className, children, ...props }: ModalProps) {
  const isDrawer = variant === "drawer";
  return (
    <ModalOverlay
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      isDismissable={isDismissable}
      className={({ isEntering, isExiting }) =>
        cx(
          "fixed inset-0 z-50 bg-black/60",
          isDrawer ? "flex justify-end" : "flex items-center justify-center p-4",
          isEntering && "duration-150 ease-out animate-in fade-in",
          isExiting && "duration-100 ease-in animate-out fade-out",
        )
      }
    >
      <AriaModal
        className={({ isEntering, isExiting }) =>
          cx(
            isDrawer && "h-full",
            isEntering && "duration-200 ease-out animate-in " + (isDrawer ? "slide-in-from-right" : "fade-in zoom-in-95"),
            isExiting && "duration-150 ease-in animate-out " + (isDrawer ? "slide-out-to-right" : "fade-out zoom-out-95"),
          )
        }
      >
        <Dialog
          aria-label={props["aria-label"]}
          className={cx(
            "cyber-chamfer-sm border border-ink-800 bg-ink-900 shadow-panel outline-none",
            isDrawer ? "h-full w-full max-w-md overflow-y-auto" : "w-full max-w-md rounded-xl p-4",
            className,
          )}
        >
          {children}
        </Dialog>
      </AriaModal>
    </ModalOverlay>
  );
}
