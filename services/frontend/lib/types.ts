export interface Source {
  index: number;
  title: string;
  url: string;
  source: string; // "notion" | "jira" | "github"
  snippet: string;
}

export interface ChatMessage {
  id?: string; // assistant messages only — needed to submit thumbs up/down feedback
  role: "user" | "assistant";
  content: string;
  sources?: Source[]; // only present on the turn that was live-streamed this session
  citedIndices?: number[];
  truncated?: boolean; // true if Gemini hit max_output_tokens before finishing
  feedback?: "up" | "down" | null;
  model?: string; // e.g. "local · qwen3.6:35b", live-streamed turns only
  elapsedMs?: number;
}

export interface SessionSummary {
  session_id: string;
  preview: string | null;
  last_active: number;
  turn_count: number;
}

export interface SessionDetail {
  session_id: string;
  history: {
    id: string;
    role: string;
    content: string;
    feedback: "up" | "down" | null;
    // Saved with each answer since sources started being persisted; null on
    // user turns and on older answers.
    sources?: Source[] | null;
    cited_indices?: number[] | null;
    model?: string | null;
    elapsed_ms?: number | null;
  }[];
}

export interface ConnectionStatus {
  id: string | null;
  provider: string;
  connected: boolean;
  workspace_name: string | null;
  site_url: string | null;
  last_synced_at: string | null;
  visibility_mode: "org_wide" | "restricted" | null;
  dead_lettered_count_24h: number;
  last_sync_status: "received" | "processing" | "succeeded" | "failed" | "dead_lettered" | null;
  // Desktop: the sign-in service refused this Jira connection's refresh token (cleared by Reconnect).
  needs_reconnect?: boolean;
  // Disconnected by an owner/admin: connected is false, id remains, document_count = documents kept.
  disconnected: boolean;
  document_count: number;
  // Only on a connected GitHub row: the repositories the app can see and where to choose them ("" = unknown).
  github?: { repo_count: number; repo_names: string[]; choose_url: string } | null;
}

export interface MeResponse {
  user_id: string;
  org_id: string;
  org_name: string;
  role: "owner" | "admin" | "member";
  email: string;
  display_name: string | null;
}

export interface OrgMember {
  user_id: string;
  email: string;
  display_name: string | null;
  role: "owner" | "admin" | "member";
}

export interface ConnectionPreview {
  visible_count: number;
  truncated: boolean;
}

export interface VoyageUsage {
  used: number;
  budget: number;
  percent: number;
}

export interface DocumentSummary {
  id: string;
  source: string;
  title: string;
  url: string;
  last_edited_at: string | null;
  synced_at: string;
  chunk_count: number;
  excluded_from_retrieval: boolean;
  // Its connection was disconnected with documents kept: still searchable, never updated.
  from_disconnected_source?: boolean;
}

export interface DocumentListResponse {
  items: DocumentSummary[];
  total: number;
}

export interface ChunkOut {
  id: string;
  chunk_index: number;
  content: string;
  token_count: number | null;
  metadata: Record<string, unknown>;
}

export interface DocumentDetail extends DocumentSummary {
  chunks: ChunkOut[];
}

export interface EventLogEntry {
  id: string;
  routing_key: string;
  dedupe_key: string;
  payload: Record<string, unknown>;
  status: string;
  error: string | null;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface DlqEvent {
  id: string;
  routing_key: string;
  error: string | null;
  created_at: string;
}

export interface InviteResult {
  token: string;
  expires_at: string;
}

export type LlmProvider = "gemini" | "anthropic" | "openai" | "local";

export interface LlmProviderConfig {
  model: string | null;
  base_url: string | null;
  has_api_key: boolean;
  api_key_hint: string | null;
}

export interface LlmSettings {
  provider: LlmProvider;
  // Saved config for every configurable provider, kept when switching.
  providers: Record<"anthropic" | "openai" | "local", LlmProviderConfig>;
  model: string | null;
  base_url: string | null;
  has_api_key: boolean;
  api_key_hint: string | null;
  default_model: string;
}

export interface LlmSettingsInput {
  provider: LlmProvider;
  model?: string | null;
  base_url?: string | null;
  // Omit to keep the saved key; remove one with deleteLlmKey().
  api_key?: string;
}

export interface LlmTestResult {
  ok: boolean;
  models: string[];
  error: string | null;
  sample: string | null;
}

export interface ServiceTokenSummary {
  id: string;
  label: string;
  created_at: string;
  revoked_at: string | null;
}

export interface ServiceTokenCreated {
  id: string;
  label: string;
  token: string; // plaintext, shown once — only the hash is persisted server-side
}
