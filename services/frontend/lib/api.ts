import type {
  ConnectionPreview,
  ConnectionStatus,
  DlqEvent,
  DocumentDetail,
  DocumentListResponse,
  DocumentSummary,
  EventLogEntry,
  InviteResult,
  MeResponse,
  OrgMember,
  ServiceTokenCreated,
  ServiceTokenSummary,
  SessionDetail,
  SessionSummary,
  VoyageUsage,
  LlmSettings,
  LlmSettingsInput,
  LlmTestResult,
} from "./types";

// Defaults to the same-origin /api proxy defined in next.config.js.
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

// No API key header anymore — auth is the httpOnly `session` cookie set by
// POST /auth/login, sent automatically via `credentials: "include"` below.
// (Previously a static NEXT_PUBLIC_API_KEY baked into the JS bundle was sent
// on every request — identical for every visitor, which is exactly the flaw
// real per-user auth replaces.)
function buildHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: string) {
    super(message);
  }
}

// Reads FastAPI's {"detail": "..."} so callers can show the server's reason.
async function apiError(res: Response, label: string): Promise<ApiError> {
  let detail: string | undefined;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    // not JSON
  }
  return new ApiError(res.status, `${label} failed: ${res.status}`, detail);
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: buildHeaders(), cache: "no-store", credentials: "include" });
  if (!res.ok) throw await apiError(res, `GET ${path}`);
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : undefined,
    credentials: "include",
  });
  if (!res.ok) throw await apiError(res, `POST ${path}`);
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
  if (!res.ok) throw await apiError(res, `PATCH ${path}`);
  return res.json();
}

async function put<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : undefined,
    credentials: "include",
  });
  if (!res.ok) throw await apiError(res, `PUT ${path}`);
  if (res.status === 204) return undefined as T;
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE", headers: buildHeaders(), credentials: "include" });
  if (!res.ok) throw await apiError(res, `DELETE ${path}`);
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

// --- Org management (owner/admin only) ---

export function inviteMember(email: string, role: string) {
  return post<InviteResult>("/auth/invite", { email, role });
}

export function listServiceTokens() {
  return get<ServiceTokenSummary[]>("/auth/service-tokens");
}

export function createServiceToken(label: string) {
  return post<ServiceTokenCreated>("/auth/service-tokens", { label });
}

export function revokeServiceToken(id: string) {
  return del<void>(`/auth/service-tokens/${id}`);
}

// --- RAG agent ---

export function getConnections() {
  return get<ConnectionStatus[]>("/connections");
}

export function syncProvider(provider: string) {
  return post<{ published: number; truncated: boolean; removed?: number }>(`/sync/${provider}`);
}

export function setConnectionVisibility(connectionId: string, mode: "org_wide" | "restricted") {
  return patch<ConnectionStatus>(`/connections/${connectionId}/visibility`, { mode });
}

export function getConnectionPreview(connectionId: string) {
  return get<ConnectionPreview>(`/connections/${connectionId}/preview`);
}

export function getVoyageUsage() {
  return get<VoyageUsage>("/usage/voyage");
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

export function setDocumentExcluded(documentId: string, excluded: boolean) {
  return patch<DocumentSummary>(`/documents/${documentId}/exclude`, { excluded });
}

export function getEvents(params: { limit?: number; status?: string; routingKey?: string }) {
  const qs = new URLSearchParams();
  qs.set("limit", String(params.limit ?? 50));
  if (params.status) qs.set("status", params.status);
  if (params.routingKey) qs.set("routing_key", params.routingKey);
  return get<EventLogEntry[]>(`/events/recent?${qs.toString()}`);
}

export function getDlqEvents() {
  return get<DlqEvent[]>("/dlq");
}

export function redriveDlqEvent(id: string) {
  return post<{ event_log_id: string }>(`/dlq/${id}/redrive`);
}

export function getSessions(search?: string) {
  const qs = new URLSearchParams();
  qs.set("limit", "30");
  if (search) qs.set("search", search);
  return get<SessionSummary[]>(`/sessions?${qs.toString()}`);
}

export function deleteSession(id: string) {
  return del<void>(`/sessions/${id}`);
}

export function getSession(id: string) {
  return get<SessionDetail>(`/sessions/${id}`);
}

export function setMessageFeedback(sessionId: string, messageId: string, feedback: "up" | "down" | null) {
  return post<void>(`/sessions/${sessionId}/messages/${messageId}/feedback`, { feedback });
}

export function getLlmSettings() {
  return get<LlmSettings>("/settings/llm");
}

export function saveLlmSettings(body: LlmSettingsInput) {
  return put<LlmSettings>("/settings/llm", body);
}

export function deleteLlmKey(provider: "anthropic" | "openai" | "local") {
  return del<LlmSettings>(`/settings/llm/keys/${provider}`);
}

export function testLlmSettings(body: LlmSettingsInput) {
  return post<LlmTestResult>("/settings/llm/test", body);
}
