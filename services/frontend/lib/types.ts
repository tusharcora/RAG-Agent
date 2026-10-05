export interface Source {
  index: number;
  title: string;
  url: string;
  source: string; // "notion" | "jira"
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
}

export interface SessionSummary {
  session_id: string;
  preview: string | null;
  last_active: number;
  turn_count: number;
}

export interface SessionDetail {
  session_id: string;
  history: { id: string; role: string; content: string; feedback: "up" | "down" | null }[];
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
