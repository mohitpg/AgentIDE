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

// ── Async tasks (Phase 3) ───────────────────────────────────────────────────
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
