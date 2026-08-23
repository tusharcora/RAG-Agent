export interface Source {
  index: number;
  title: string;
  url: string;
  source: string; // "notion" | "jira"
  snippet: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: Source[]; // only present on the turn that was live-streamed this session
  citedIndices?: number[];
}

export interface SessionSummary {
  session_id: string;
  preview: string | null;
  last_active: number;
  turn_count: number;
}

export interface SessionDetail {
  session_id: string;
  history: { role: string; content: string }[];
}

export interface ConnectionStatus {
  id: string | null;
  provider: string;
  connected: boolean;
  workspace_name: string | null;
  site_url: string | null;
  last_synced_at: string | null;
  visibility_mode: "org_wide" | "restricted" | null;
}

export interface MeResponse {
  user_id: string;
  org_id: string;
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

export interface DocumentSummary {
  id: string;
  source: string;
  title: string;
  url: string;
  last_edited_at: string | null;
  synced_at: string;
  chunk_count: number;
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
