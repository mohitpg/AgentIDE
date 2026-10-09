// Mirrors apps/api DTOs and packages/schemas StreamEvent.

// A project scopes all user data (chats, tasks, uploads, indexed docs).
export interface Project {
  id: string
  name: string
  created_at: string
  updated_at: string
}

export interface Session {
  id: string
  title: string | null
  created_at: string
  updated_at: string
}

// One intermediate specialist step shown as a collapsible "Thinking" disclosure
// on an assistant message (built live from agent_step + thinking-channel tokens).
export interface AgentStep {
  agent: string
  skill?: string | null
  content: string
  done: boolean
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  run_id?: string | null
  created_at: string
  // Intermediate supervisor-loop steps (assistant messages only; not persisted).
  steps?: AgentStep[]
}

// Supervisor execution mode chosen in the composer; sent with each message.
export type ChatMode = 'auto' | 'agent' | 'chat'

export interface SessionDetail extends Session {
  messages: Message[]
}

// ── Async tasks ───────────────────────────────────────────────────
export type TaskStatus =
  | 'pending'
  | 'started'
  | 'progress'
  | 'success'
  | 'failure'
  | 'revoked'

export interface Task {
  id: string
  type: string
  label: string | null
  queue: string
  status: TaskStatus
  progress: number
  message: string | null
  params: Record<string, unknown>
  result: Record<string, unknown> | null
  error: string | null
  session_id: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export const TASK_TERMINAL: TaskStatus[] = ['success', 'failure', 'revoked']

// ── RAG ───────────────────────────────────────────────────────────
export interface RagDocument {
  id: string
  filename: string
  content_type: string | null
  source: string
  session_id: string | null
  num_chunks: number
  byte_size: number | null
  created_at: string
}

export interface IngestResponse {
  document_id: string
  filename: string
  num_chunks: number
}

export interface RagSearchHit {
  chunk_id: string
  document_id: string
  filename: string
  chunk_index: number
  content: string
  score: number
  sources: string[]
}

export interface RagSearchResponse {
  query: string
  hits: RagSearchHit[]
}

// ── MCP servers ───────────────────────────────────────────────────
export type MCPTransport = 'stdio' | 'sse'

export interface MCPServer {
  id: string
  name: string
  description: string | null
  transport: MCPTransport
  command: string
  args: string[]
  env: Record<string, string>
  cwd: string | null
  url: string
  enabled: boolean
  allowed_agents: string[]
  tool_allowlist: string[]
  created_at: string
  updated_at: string
}

export interface MCPTool {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

export interface MCPDiscoverResponse {
  server_id: string
  connected: boolean
  tools: MCPTool[]
  error: string | null
}

// Agents the UI can assign MCP servers to (mirrors the agent/skill catalog).
export const KNOWN_AGENTS = ['code', 'rag', 'summarizer', 'vision', 'web'] as const

// ── Streaming events (unified schema, switched on `type`) ───────────────────
export type EventType =
  | 'token'
  | 'agent_step'
  | 'tool_call'
  | 'tool_result'
  | 'task_progress'
  | 'error'
  | 'done'

export interface StreamEvent {
  id: string
  type: EventType
  run_id: string
  data: Record<string, unknown>
}
