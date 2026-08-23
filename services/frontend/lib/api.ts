import type {
  ConnectionStatus,
  DocumentDetail,
  DocumentListResponse,
  EventLogEntry,
  SessionDetail,
  SessionSummary,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
// Kept forward-compatible with API_SHARED_SECRET being turned on later — unset by default.
export const API_KEY_HEADER = process.env.NEXT_PUBLIC_API_KEY;

function buildHeaders(): HeadersInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY_HEADER) headers["X-API-Key"] = API_KEY_HEADER;
  return headers;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: buildHeaders(), cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
  return res.json();
}

export function getConnections() {
  return get<ConnectionStatus[]>("/connections");
}

export function syncProvider(provider: string) {
  return post<{ published: number; truncated: boolean }>(`/sync/${provider}`);
}

export function getDocuments(params: { source?: string; search?: string; limit?: number; offset?: number }) {
  const qs = new URLSearchParams();
  if (params.source) qs.set("source", params.source);
  if (params.search) qs.set("search", params.search);
  qs.set("limit", String(params.limit ?? 25));
  qs.set("offset", String(params.offset ?? 0));
  return get<DocumentListResponse>(`/documents?${qs.toString()}`);
}

export function getDocument(id: string) {
  return get<DocumentDetail>(`/documents/${id}`);
}

export function getEvents(params: { limit?: number; status?: string; routingKey?: string }) {
  const qs = new URLSearchParams();
  qs.set("limit", String(params.limit ?? 50));
  if (params.status) qs.set("status", params.status);
  if (params.routingKey) qs.set("routing_key", params.routingKey);
  return get<EventLogEntry[]>(`/events/recent?${qs.toString()}`);
}

export function getSessions() {
  return get<SessionSummary[]>("/sessions?limit=30");
}

export function getSession(id: string) {
  return get<SessionDetail>(`/sessions/${id}`);
}
