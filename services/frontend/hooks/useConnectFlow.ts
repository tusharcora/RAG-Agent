"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cancelConnect, connectErrorMessage, pollConnect, startConnect, type ConnectPurpose } from "@/lib/connect";

export type ConnectFlow =
  | { phase: "idle" }
  | { phase: "waiting"; purpose: ConnectPurpose; url: string }
  | { phase: "error"; purpose: ConnectPurpose; message: string };

/**
 * One browser sign-in at a time: start, open the provider page in the system
 * browser, poll until it finishes. `onDone` fires after success; `onSettled`
 * after any end (done, error or cancel) so callers can refresh what they show.
 */
export function useConnectFlow({ onDone, onSettled }: { onDone?: (purpose: ConnectPurpose) => void; onSettled?: () => void }) {
  const [flow, setFlow] = useState<ConnectFlow>({ phase: "idle" });
  const [starting, setStarting] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const stateRef = useRef<string | null>(null);
  const cb = useRef({ onDone, onSettled });
  cb.current = { onDone, onSettled };

  useEffect(() => () => abortRef.current?.abort(), []);

  const startingRef = useRef(false);

  const start = useCallback(async (purpose: ConnectPurpose) => {
    if (startingRef.current) return; // a double-click must not open two provider windows
    // Replacing a waiting flow: drop the old one on the server (fire-and-forget).
    const old = stateRef.current;
    if (old) cancelConnect(old).catch(() => {});
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    stateRef.current = null;
    startingRef.current = true;
    setStarting(true);
    try {
      const { state, url } = await startConnect(purpose);
      startingRef.current = false;
      setStarting(false);
      if (ctrl.signal.aborted) {
        cancelConnect(state).catch(() => {});
        return;
      }
      stateRef.current = state;
      setFlow({ phase: "waiting", purpose, url });
      window.open(url, "_blank", "noopener");
      await pollConnect(state, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      stateRef.current = null;
      setFlow({ phase: "idle" });
      cb.current.onDone?.(purpose);
      cb.current.onSettled?.();
    } catch (err) {
      startingRef.current = false;
      setStarting(false);
      if (ctrl.signal.aborted) return;
      stateRef.current = null;
      setFlow({ phase: "error", purpose, message: connectErrorMessage(err) });
      cb.current.onSettled?.();
    }
  }, []);

  const cancel = useCallback(async () => {
    abortRef.current?.abort();
    const state = stateRef.current;
    stateRef.current = null;
    setFlow({ phase: "idle" });
    if (state) await cancelConnect(state).catch(() => {});
    // A cancel during an in-flight redeem may still save: refresh either way.
    cb.current.onSettled?.();
  }, []);

  const reopen = useCallback(() => {
    if (flow.phase === "waiting") window.open(flow.url, "_blank", "noopener");
  }, [flow]);

  return { flow, start, cancel, reopen, starting };
}
