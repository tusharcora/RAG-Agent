import { API_BASE, ApiError } from "./api";

export interface SetupStatus {
  setup_complete: boolean;
  embedding_provider: string;
  ollama: { running: boolean; models: string[] };
  required_models: string[];
  answer_model_candidates: string[];
  /** Installed name of the search model under any tag, or null if it must be downloaded. */
  embed_model: string | null;
}

export interface PullProgress {
  completed?: number;
  total?: number;
  status: string;
}

export type KeysProvider = "anthropic" | "openai" | "gemini";

export interface CompleteKeysBody {
  voyage_api_key: string;
  provider: KeysProvider;
  api_key: string;
  model?: string;
}

/** A 400 from /setup/complete-keys that names the input it is about. */
export class SetupFieldError extends ApiError {
  constructor(status: number, public field: string, public fieldMessage: string) {
    super(status, fieldMessage, fieldMessage);
  }
}

export async function getSetupStatus(): Promise<SetupStatus> {
  const res = await fetch(`${API_BASE}/setup/status`, { cache: "no-store", credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, `GET /setup/status failed: ${res.status}`);
  return res.json();
}

async function errorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // not JSON
  }
  return fallback;
}

/**
 * Downloads one model through the API, reporting progress as it streams.
 * Resolves on the `done` event; rejects with the `error` event's message.
 */
export async function pullModel(model: string, onProgress: (p: PullProgress) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(`${API_BASE}/setup/pull`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
    credentials: "include",
    signal,
  });
  if (!res.ok || !res.body) throw new Error(await errorMessage(res, `Download failed: ${res.status}`));

  const reader = res.body.getReader();
  // One streaming decoder across chunks, same as lib/sse.ts.
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let outcome: "done" | Error | null = null;

  const dispatch = (raw: string) => {
    let name = "message";
    let data = "";
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    let parsed: any;
    try {
      parsed = JSON.parse(data);
    } catch {
      return;
    }
    if (name === "progress") {
      // Status-only lines ("writing manifest") carry total 0: leave both undefined so callers keep
      // the last known values. A known total always comes with its own completed, including 0
      // (a new layer starting).
      const known = typeof parsed.total === "number" && parsed.total > 0;
      onProgress({ completed: known ? parsed.completed ?? 0 : undefined, total: known ? parsed.total : undefined, status: parsed.status ?? "" });
    }
    else if (name === "done") outcome = "done";
    else if (name === "error") outcome = new Error(parsed.message ?? "Download failed");
  };

  while (outcome === null) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      dispatch(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
  }
  // `outcome` is assigned inside dispatch(), which TS flow analysis cannot see.
  const result = outcome as "done" | Error | null;
  if (result === "done") return;
  if (result instanceof Error) throw result;
  throw new Error("The download stopped unexpectedly.");
}

export async function completeLocal(answerModel: string): Promise<void> {
  const res = await fetch(`${API_BASE}/setup/complete-local`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer_model: answerModel }),
    credentials: "include",
  });
  if (!res.ok) throw new ApiError(res.status, `POST /setup/complete-local failed: ${res.status}`, await errorMessage(res, ""));
}

export async function completeKeys(body: CompleteKeysBody): Promise<void> {
  const res = await fetch(`${API_BASE}/setup/complete-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (res.ok) return;
  let detail: unknown;
  try {
    detail = (await res.json())?.detail;
  } catch {
    // not JSON
  }
  if (detail && typeof detail === "object" && "field" in detail && "message" in detail) {
    const d = detail as { field: string; message: string };
    throw new SetupFieldError(res.status, d.field, d.message);
  }
  throw new ApiError(res.status, `POST /setup/complete-keys failed: ${res.status}`, typeof detail === "string" ? detail : undefined);
}

const HEALTH_POLL_MS = 1000;
const HEALTH_STEADY_MS = 30_000;
const HEALTH_GIVE_UP_MS = 90_000;

/**
 * Waits out the API restart that follows setup: resolves once /health has
 * failed at least once and then answered again, or after 30 s of answering
 * (the restart can be quick enough that no poll ever lands in the gap).
 * Rejects if the API hasn't answered after 90 s.
 */
export async function waitForRestart(): Promise<void> {
  const started = Date.now();
  let sawFailure = false;
  while (true) {
    let ok = false;
    try {
      // Bounded: a backend that accepts the connection but never answers
      // must still count as "not back yet" rather than hang this loop.
      const res = await fetch(`${API_BASE}/health`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
      ok = res.ok;
    } catch {
      ok = false;
    }
    const elapsed = Date.now() - started;
    if (ok) {
      if (sawFailure || elapsed >= HEALTH_STEADY_MS) return;
    } else {
      sawFailure = true;
      if (elapsed >= HEALTH_GIVE_UP_MS) throw new Error(
          "The app didn't come back after restarting. Your setup is saved: quit Knowledge Agent (or close its Terminal window) and start it again.",
        );
    }
    await new Promise((r) => setTimeout(r, HEALTH_POLL_MS));
  }
}

// --- formatting / helpers ---

/** Ollama lists "llama3.2:latest" for a model pulled as "llama3.2". */
/**
 * The installed name for `model`: an exact match first (ignoring ":latest"),
 * otherwise the same model under another tag ("nomic-embed-text" matches
 * "nomic-embed-text:v1.5"). Mirrors the API's `_installed_as`.
 */
export function installedName(model: string, installed: string[]): string | null {
  const strip = (m: string) => m.replace(/:latest$/, "");
  const base = strip(model);
  const exact = installed.find((m) => strip(m) === base);
  if (exact) return strip(exact);
  if (base.includes(":")) return null;
  return installed.find((m) => m.split(":", 1)[0] === base) ?? null;
}

export function isInstalled(model: string, installed: string[]): boolean {
  return installedName(model, installed) !== null;
}

function sizeParts(bytes: number): { value: string; unit: "GB" | "MB" } {
  return bytes >= 1e9
    ? { value: (bytes / 1e9).toFixed(1), unit: "GB" }
    : { value: Math.round(bytes / 1e6).toString(), unit: "MB" };
}

export function formatBytes(bytes: number): string {
  const { value, unit } = sizeParts(bytes);
  return `${value} ${unit}`;
}

/** "1.2 of 2.0 GB", or "480 of 700 MB". */
export function formatProgress(completed: number, total: number): string {
  const c = sizeParts(completed);
  const t = sizeParts(total);
  return c.unit === t.unit ? `${c.value} of ${t.value} ${t.unit}` : `${c.value} ${c.unit} of ${t.value} ${t.unit}`;
}
