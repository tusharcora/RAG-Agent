import type {
  ConnectionPreview,
  ConnectionStatus,
  DocumentDetail,
  DocumentListResponse,
  EventLogEntry,
  MeResponse,
  OrgMember,
  SessionDetail,
  SessionSummary,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// No API key header anymore — auth is the httpOnly `session` cookie set by
// POST /auth/login, sent automatically via `credentials: "include"` below.
// (Previously a static NEXT_PUBLIC_API_KEY baked into the JS bundle was sent
// on every request — identical for every visitor, which is exactly the flaw
// real per-user auth replaces.)
function buildHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: buildHeaders(), cache: "no-store", credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, `GET ${path} failed: ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : undefined,
    credentials: "include",
  });
  if (!res.ok) throw new ApiError(res.status, `POST ${path} failed: ${res.status}`);
  if (res.status === 204) return undefined as T;
  return res.json();
}

async function patch<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : undefined,
    credentials: "include",
  });
  if (!res.ok) throw new ApiError(res.status, `PATCH ${path} failed: ${res.status}`);
  return res.json();
}

async function put<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : undefined,
    credentials: "include",
  });
  if (!res.ok) throw new ApiError(res.status, `PUT ${path} failed: ${res.status}`);
  if (res.status === 204) return undefined as T;
  return res.json();
}

// --- Auth ---

export function signup(request: { email: string; password: string; display_name?: string; org_name?: string; invite_token?: string }) {
  return post<MeResponse>("/auth/signup", request);
}

export function login(email: string, password: string) {
  return post<MeResponse>("/auth/login", { email, password });
}

export function logout() {
  return post<void>("/auth/logout");
}

export function me() {
  return get<MeResponse>("/auth/me");
}

export function getOrgMembers() {
  return get<OrgMember[]>("/org/members");
}

// --- RAG agent ---

export function getConnections() {
  return get<ConnectionStatus[]>("/connections");
}

export function syncProvider(provider: string) {
  return post<{ published: number; truncated: boolean }>(`/sync/${provider}`);
}

export function setConnectionVisibility(connectionId: string, mode: "org_wide" | "restricted") {
  return patch<ConnectionStatus>(`/connections/${connectionId}/visibility`, { mode });
}

export function getConnectionPreview(connectionId: string) {
  return get<ConnectionPreview>(`/connections/${connectionId}/preview`);
}

export function getConnectionMembers(connectionId: string) {
  return get<string[]>(`/connections/${connectionId}/members`);
}

export function setConnectionMembers(connectionId: string, userIds: string[]) {
  return put<void>(`/connections/${connectionId}/members`, { user_ids: userIds });
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
