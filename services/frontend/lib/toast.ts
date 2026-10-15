"use client";

// Built on react-aria-components' own toast primitives (UNSTABLE_ToastQueue /
// UNSTABLE_ToastRegion / UNSTABLE_Toast, rendered by
// components/base/toast/toast-region.tsx) instead of adding a dependency
// like sonner — every other primitive in components/base/* is already
// react-aria-components-backed.
//
// Risk: the `UNSTABLE_` prefix means this API can change shape on a minor
// version bump with no deprecation window. react-aria-components is pinned
// to an exact version in package.json (no `^`) specifically because of
// this — if an upgrade breaks toast rendering, check react-aria-components'
// changelog for Toast/ToastQueue before assuming the bug is in this file.
import { UNSTABLE_ToastQueue as ToastQueue } from "react-aria-components";

export interface ToastContent {
  title: string;
  description?: string;
  variant: "success" | "error" | "info";
}

// Module-level singleton so any component can call toast.success(...) etc.
// without prop-drilling a queue — mirrors lib/api.ts's approach of plain
// exported functions rather than a context/hook for something used this
// broadly.
export const toastQueue = new ToastQueue<ToastContent>({ maxVisibleToasts: 4 });

function show(variant: ToastContent["variant"], title: string, description?: string) {
  return toastQueue.add({ title, description, variant }, { timeout: variant === "error" ? 6000 : 4000 });
}

export const toast = {
  success: (title: string, description?: string) => show("success", title, description),
  error: (title: string, description?: string) => show("error", title, description),
  info: (title: string, description?: string) => show("info", title, description),
};
