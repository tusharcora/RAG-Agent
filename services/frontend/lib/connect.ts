import { API_BASE, ApiError } from "./api";

export type ConnectPurpose = "notion" | "jira" | "github_repos" | "google" | "github";

export interface LinkedIdentity {
  provider: string;
  email: string | null;
}

export interface Account {
  signed_in: boolean;
  provider: string | null;
  email: string | null;
  name: string | null;
  /** Linked sign-in identities, oldest first; empty when signed out. */
  identities: LinkedIdentity[];
  broker_configured: boolean;
  prompt_seen: boolean;
}

export const EXPIRED_MESSAGE = "This sign-in link has expired. Start again from the app.";

async function request<T>(method: "GET" | "POST", path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    credentials: "include",
  });
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, `${method} ${path} failed: ${res.status}`, detail);
  }
  return res.json();
}

/** Begins a sign-in. The caller opens `url` in the system browser. */
export function startConnect(purpose: ConnectPurpose): Promise<{ state: string; url: string }> {
  return request("POST", `/connect/${purpose}/start`);
}

export function cancelConnect(state: string): Promise<void> {
  return request<void>("POST", `/connect/${encodeURIComponent(state)}/cancel`).then(() => undefined);
}

/**
 * Polls until the browser sign-in finishes. Resolves "done"; rejects with the
 * status message on failure, or when the flow expires or the user cancels.
 */
export async function pollConnect(
  state: string,
  { intervalMs = 2000, timeoutMs = 600000, signal }: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<"done"> {
  const deadline = Date.now() + timeoutMs;
  while (true) {
    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    try {
      const s = await request<{ status: "pending" | "done" | "error"; message: string | null }>(
        "GET",
        `/connect/status/${encodeURIComponent(state)}`,
      );
      if (s.status === "done") return "done";
      if (s.status === "error") throw new Error(s.message || EXPIRED_MESSAGE);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) throw new Error(EXPIRED_MESSAGE);
      if (err instanceof ApiError || !(err instanceof TypeError)) throw err;
      // A network blip (API restarting): keep waiting until the deadline.
    }
    if (Date.now() >= deadline) throw new Error(EXPIRED_MESSAGE);
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(resolve, intervalMs);
      signal?.addEventListener(
        "abort",
        () => {
          clearTimeout(t);
          reject(new DOMException("Cancelled", "AbortError"));
        },
        { once: true },
      );
    });
  }
}

export function getAccount(): Promise<Account> {
  return request<Account>("GET", "/account");
}

export function signOut(): Promise<void> {
  return request<void>("POST", "/account/sign-out").then(() => undefined);
}

/** Removes one linked sign-in. Removing the last one signs out fully. */
export function unlink(provider: "google" | "github"): Promise<void> {
  return request<void>("POST", `/account/unlink/${provider}`).then(() => undefined);
}

export function markPromptSeen(): Promise<void> {
  return request<void>("POST", "/account/prompt-seen").then(() => undefined);
}

/** Message to show for a failed start/poll. */
export function connectErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.detail) return err.detail;
  if (err instanceof Error && !(err instanceof ApiError) && err.message) return err.message;
  return "Couldn't reach the sign-in service. Check your internet connection.";
}
