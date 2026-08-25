"use client";

import type { QueuedToast } from "react-aria-components";
import { UNSTABLE_Toast as Toast, UNSTABLE_ToastRegion as ToastRegion, Text, Button as AriaButton } from "react-aria-components";
import { AlertCircle, CheckCircle, InfoCircle, X } from "@untitledui/icons";
import { toastQueue, type ToastContent } from "@/lib/toast";
import { cx } from "@/utils/cx";

const VARIANT_ICON: Record<ToastContent["variant"], { icon: typeof CheckCircle; className: string }> = {
  success: { icon: CheckCircle, className: "text-status-succeeded" },
  error: { icon: AlertCircle, className: "text-coral-400" },
  info: { icon: InfoCircle, className: "text-gold-400" },
};

/**
 * Mounted once in app/layout.tsx. Renders nothing until something calls
 * toast.success/error/info from lib/toast.ts.
 */
export function ToastRegionMount() {
  return (
    <ToastRegion queue={toastQueue} className="fixed bottom-4 right-4 z-[100] flex flex-col-reverse gap-2 outline-none">
      {({ toast: queuedToast }) => <ToastItem toast={queuedToast} />}
    </ToastRegion>
  );
}

function ToastItem({ toast: queuedToast }: { toast: QueuedToast<ToastContent> }) {
  const { icon: Icon, className } = VARIANT_ICON[queuedToast.content.variant];
  return (
    <Toast
      toast={queuedToast}
      className="cyber-chamfer-sm flex w-80 animate-in items-start gap-2.5 rounded-xl border border-ink-800 bg-ink-900 p-3 shadow-panel outline-none duration-200 ease-out fade-in slide-in-from-bottom-2"
    >
      <Icon className={cx("mt-0.5 h-4 w-4 shrink-0", className)} />
      <div className="min-w-0 flex-1">
        <Text slot="title" className="text-sm font-medium text-ink-100">
          {queuedToast.content.title}
        </Text>
        {queuedToast.content.description && (
          <Text slot="description" className="mt-0.5 text-xs text-ink-500">
            {queuedToast.content.description}
          </Text>
        )}
      </div>
      <AriaButton slot="close" aria-label="Dismiss" className="shrink-0 rounded-md p-0.5 text-ink-600 outline-none transition hover:text-ink-300 focus-visible:outline-2 focus-visible:outline-coral-500">
        <X className="h-3.5 w-3.5" />
      </AriaButton>
    </Toast>
  );
}
