import { useCallback, useEffect, useRef, useState } from 'react'

import { cancelTask, createTask, listTasks, TASKS_WS_URL, type CreateTaskBody } from '../lib/api'
import {
  refreshAccessToken,
  socketAuthFrame,
  WS_FORBIDDEN,
  WS_UNAUTHORIZED,
} from '../lib/auth'
import type { StreamEvent, Task, TaskStatus } from '../types'

export interface UseTasks {
  tasks: Task[]
  connected: boolean
  enqueue: (body?: CreateTaskBody) => Promise<void>
  cancel: (id: string) => Promise<void>
  refresh: () => void
}

/**
 * Owns the task list and one WebSocket to /tasks/ws. The REST list is the
 * baseline; `task_progress` events patch rows live (status, progress, result).
 * Independent of the chat socket, so tasks never block chat.
 */
export function useTasks(): UseTasks {
  const [tasks, setTasks] = useState<Task[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const refresh = useCallback((): void => {
    listTasks()
      .then(setTasks)
      .catch(() => {
        /* backend down — keep what we have */
      })
  }, [])

  // Initial load.
  useEffect(() => {
    refresh()
  }, [refresh])

  // Apply one streamed task event onto the list.
  const applyEvent = useCallback(
    (event: StreamEvent): void => {
      if (event.type !== 'task_progress') return
      const data = event.data as {
        task_id?: string
        status?: TaskStatus
        progress?: number
        message?: string
        result?: Record<string, unknown> | null
        error?: string | null
      }
      const id = data.task_id
      if (!id) return

      setTasks((prev) => {
        const idx = prev.findIndex((t) => t.id === id)
        if (idx === -1) {
          // A task we don't know about yet (created elsewhere) — resync the list.
          refresh()
          return prev
        }
        const next = prev.slice()
        const t = next[idx]
        next[idx] = {
          ...t,
          status: data.status ?? t.status,
          progress: typeof data.progress === 'number' ? data.progress : t.progress,
          message: data.message ?? t.message,
          result: data.result !== undefined ? data.result : t.result,
          error: data.error !== undefined ? data.error : t.error,
        }
        return next
      })
    },
    [refresh],
  )

  // Persistent WebSocket with simple reconnect (mirrors useChat, including the
  // first-frame auth: the server only streams this user + project's tasks).
  useEffect(() => {
    let closed = false
    let retry: ReturnType<typeof setTimeout>

    const connect = (): void => {
      const ws = new WebSocket(TASKS_WS_URL)
      wsRef.current = ws
      ws.onopen = () => {
        ws.send(socketAuthFrame())
        setConnected(true)
        // Re-sync after a (re)connect in case we missed events while down.
        refresh()
      }
      ws.onclose = (ev) => {
        setConnected(false)
        if (closed || ev.code === WS_FORBIDDEN) return
        const ready = ev.code === WS_UNAUTHORIZED ? refreshAccessToken() : Promise.resolve(null)
        void ready.then(() => {
          if (!closed) retry = setTimeout(connect, 1500)
        })
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (ev) => applyEvent(JSON.parse(ev.data as string) as StreamEvent)
    }

    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      wsRef.current?.close()
    }
  }, [applyEvent, refresh])

  const enqueue = useCallback(async (body: CreateTaskBody = {}): Promise<void> => {
    const task = await createTask(body)
    setTasks((prev) => [task, ...prev.filter((t) => t.id !== task.id)])
  }, [])

  const cancel = useCallback(async (id: string): Promise<void> => {
    await cancelTask(id)
    // Optimistic: the revoked event will confirm and finalize the row.
    setTasks((prev) =>
      prev.map((t) =>
        t.id === id ? { ...t, message: 'Canceling…' } : t,
      ),
    )
  }, [])

  return { tasks, connected, enqueue, cancel, refresh }
}
