// Mirrors apps/api DTOs and packages/schemas StreamEvent.

export interface Session {
  id: string
  title: string | null
  created_at: string
  updated_at: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  run_id?: string | null
  created_at: string
}

export interface SessionDetail extends Session {
  messages: Message[]
}

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
