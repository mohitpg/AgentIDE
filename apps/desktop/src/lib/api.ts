// REST helpers + WebSocket URL for the chat backend.

import type { Session, SessionDetail, Task } from '../types'

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

export async function createSession(title?: string): Promise<Session> {
  return json(
    await fetch(`${API_BASE}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title ?? null }),
    }),
  )
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
