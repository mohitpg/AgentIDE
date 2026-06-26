// REST helpers + WebSocket URL for the chat backend.

import type {
  IngestResponse,
  MCPDiscoverResponse,
  MCPServer,
  RagDocument,
  RagSearchResponse,
  Session,
  SessionDetail,
  Task,
} from '../types'

export const API_BASE: string = window.api?.baseUrl ?? 'http://localhost:8000'
export const WS_URL: string = `${API_BASE.replace(/^http/, 'ws')}/chat/ws`
export const TASKS_WS_URL: string = `${API_BASE.replace(/^http/, 'ws')}/tasks/ws`

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export async function listSessions(): Promise<Session[]> {
  return json(await fetch(`${API_BASE}/sessions`))
}

export async function getSession(id: string): Promise<SessionDetail> {
  return json(await fetch(`${API_BASE}/sessions/${id}`))
}

export async function deleteSession(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
}

// ── Tasks (Phase 3) ─────────────────────────────────────────────────────────
export async function listTasks(): Promise<Task[]> {
  return json(await fetch(`${API_BASE}/tasks`))
}

export interface CreateTaskBody {
  type?: string
  label?: string
  params?: Record<string, unknown>
  session_id?: string | null
}

export async function createTask(body: CreateTaskBody = {}): Promise<Task> {
  return json(
    await fetch(`${API_BASE}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'mock', ...body }),
    }),
  )
}

export async function cancelTask(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${id}/cancel`, { method: 'POST' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
}

export async function getTask(id: string): Promise<Task> {
  return json(await fetch(`${API_BASE}/tasks/${id}`))
}

// ── RAG (Phase 4) ─────────────────────────────────────────────────────────────
export async function uploadDocument(
  file: File,
  sessionId?: string | null,
): Promise<IngestResponse> {
  const form = new FormData()
  form.append('file', file)
  if (sessionId) form.append('session_id', sessionId)
  const res = await fetch(`${API_BASE}/rag/documents`, { method: 'POST', body: form })
  if (!res.ok) {
    // Surface the backend's error detail (e.g. unsupported file, too large).
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<IngestResponse>
}

// Async ingestion: returns a Task immediately; index runs on the Celery worker
// and progress streams over /tasks/ws (or poll GET /tasks/{id}).
export async function uploadDocumentAsync(
  file: File,
  sessionId?: string | null,
): Promise<Task> {
  const form = new FormData()
  form.append('file', file)
  if (sessionId) form.append('session_id', sessionId)
  const res = await fetch(`${API_BASE}/rag/documents/async`, { method: 'POST', body: form })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<Task>
}

export async function listDocuments(): Promise<RagDocument[]> {
  return json(await fetch(`${API_BASE}/rag/documents`))
}

// ── Images (chat attachments) ─────────────────────────────────────────────────
export interface ImageUploadResponse {
  id: string
  // The `<Image at db.images.{id}>` token to embed in the chat message.
  ref: string
  filename: string
  content_type: string
}

// Stage an attached image and get back the reference to embed in the message.
export async function uploadImage(
  file: File,
  sessionId?: string | null,
): Promise<ImageUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  if (sessionId) form.append('session_id', sessionId)
  const res = await fetch(`${API_BASE}/images`, { method: 'POST', body: form })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<ImageUploadResponse>
}

// URL that serves a stored image's bytes — used for message-bubble thumbnails.
export function imageUrl(id: string): string {
  return `${API_BASE}/images/${id}`
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/rag/documents/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
}

export async function searchRag(query: string, topK?: number): Promise<RagSearchResponse> {
  return json(
    await fetch(`${API_BASE}/rag/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, top_k: topK ?? null }),
    }),
  )
}

// ── MCP servers (Phase 6) ─────────────────────────────────────────────────────
export type MCPServerCreate = Pick<MCPServer, 'id'> &
  Partial<Omit<MCPServer, 'id' | 'created_at' | 'updated_at'>>

export type MCPServerUpdate = Partial<Omit<MCPServer, 'id' | 'created_at' | 'updated_at'>>

export async function listMcpServers(): Promise<MCPServer[]> {
  return json(await fetch(`${API_BASE}/mcp`))
}

export async function listMcpBuiltins(): Promise<MCPServerCreate[]> {
  return json(await fetch(`${API_BASE}/mcp/builtins`))
}

export async function createMcpServer(body: MCPServerCreate): Promise<MCPServer> {
  const res = await fetch(`${API_BASE}/mcp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<MCPServer>
}

export async function updateMcpServer(id: string, body: MCPServerUpdate): Promise<MCPServer> {
  return json(
    await fetch(`${API_BASE}/mcp/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function deleteMcpServer(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/mcp/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
}

export async function discoverMcpServer(id: string): Promise<MCPDiscoverResponse> {
  return json(await fetch(`${API_BASE}/mcp/${id}/discover`, { method: 'POST' }))
}
