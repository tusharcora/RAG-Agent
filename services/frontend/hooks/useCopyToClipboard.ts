"use client";

import { useCallback } from "react";
import { toast } from "@/lib/toast";

/**
 * Wraps navigator.clipboard.writeText with toast feedback — write once, reuse
 * for every copy button (chat message copy, chunk copy, service-token copy,
 * activity trace-ID copy) instead of each one silently succeeding/failing
 * with no signal to the user.
 */
export function useCopyToClipboard() {
  return useCallback((text: string, successMessage = "Copied to clipboard") => {
    navigator.clipboard
      .writeText(text)
      .then(() => toast.success(successMessage))
      .catch(() => toast.error("Couldn't copy", "Your browser blocked clipboard access."));
  }, []);
}
