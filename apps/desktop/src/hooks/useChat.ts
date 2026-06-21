import { useCallback, useEffect, useRef, useState } from 'react'

import { getSession, WS_URL } from '../lib/api'
import { applyTraceEvent, type TraceRun } from '../lib/trace'
import type { Message, StreamEvent } from '../types'

type Status = 'idle' | 'streaming'

export interface UseChat {
  messages: Message[]
  status: Status
  connected: boolean
  trace: TraceRun[]
  send: (content: string) => void
}

export interface UseChatOptions {
  // A draft session has a client-generated id but no DB row yet, so there is
  // no history to load — it is persisted server-side on the first message.
  isDraft?: boolean
  // Called once when a draft's first message has been sent (and the turn ends),
  // i.e. the session now exists in the DB and should appear in history.
  onPersisted?: () => void
}

/**
 * Owns one WebSocket to /chat/ws and the message list for `sessionId`.
 * Token events stream into a placeholder assistant message; `done` finalizes it.
 */
export function useChat(sessionId: string | null, opts: UseChatOptions = {}): UseChat {
  const [messages, setMessages] = useState<Message[]>([])
  const [status, setStatus] = useState<Status>('idle')
  const [connected, setConnected] = useState(false)
  // Live agent trace for this session's runs (not persisted — cleared on switch).
  const [trace, setTrace] = useState<TraceRun[]>([])

  const wsRef = useRef<WebSocket | null>(null)
  const streamingIdRef = useRef<string | null>(null)
  // Latest draft state / callback, kept in refs so they don't re-run effects.
  const isDraftRef = useRef(opts.isDraft ?? false)
  isDraftRef.current = opts.isDraft ?? false
  const onPersistedRef = useRef(opts.onPersisted)
  onPersistedRef.current = opts.onPersisted
  // Set when a draft's first message is sent; flushed when the turn finishes.
  const pendingPersistRef = useRef(false)

  // Load history (and reset the trace) whenever the active session changes.
  useEffect(() => {
    setTrace([])
    if (!sessionId || isDraftRef.current) {
      // A draft starts empty; no DB row to fetch.
      setMessages([])
      return
    }
    let cancelled = false
    getSession(sessionId)
      .then((s) => {
        if (!cancelled) setMessages(s.messages)
      })
      .catch(() => {
        if (!cancelled) setMessages([])
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  // Persistent WebSocket with simple reconnect.
  useEffect(() => {
    let closed = false
    let retry: ReturnType<typeof setTimeout>

    const connect = (): void => {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = setTimeout(connect, 1500)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data as string) as StreamEvent)
    }

    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      wsRef.current?.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A draft's first turn persists the session server-side; tell the parent so
  // the session list can pick it up. Fires on done or error, since the user
  // turn is committed before streaming starts.
  const flushPersist = (): void => {
    if (pendingPersistRef.current) {
      pendingPersistRef.current = false
      onPersistedRef.current?.()
    }
  }

  const handleEvent = useCallback((event: StreamEvent): void => {
    // Every event (except plain tokens) updates the agent trace.
    setTrace((prev) => applyTraceEvent(prev, event))

    switch (event.type) {
      case 'token': {
        const chunk = String(event.data.content ?? '')
        setMessages((prev) =>
          prev.map((m) =>
            m.id === streamingIdRef.current ? { ...m, content: m.content + chunk } : m,
          ),
        )
        break
      }
      case 'error': {
        const msg = String(event.data.message ?? 'Something went wrong')
        setMessages((prev) =>
          prev.map((m) =>
            m.id === streamingIdRef.current
              ? { ...m, content: m.content || `⚠️ ${msg}` }
              : m,
          ),
        )
        streamingIdRef.current = null
        setStatus('idle')
        flushPersist()
        break
      }
      case 'done': {
        streamingIdRef.current = null
        setStatus('idle')
        flushPersist()
        break
      }
      // agent_step / tool_* are surfaced in the Phase 2 trace panel (above).
      default:
        break
    }
  }, [])

  const send = useCallback(
    (content: string): void => {
      const ws = wsRef.current
      if (!sessionId || !content.trim() || status === 'streaming') return
      if (!ws || ws.readyState !== WebSocket.OPEN) return

      // First message on a draft: it gets persisted server-side now. (Once
      // persisted the parent clears the draft flag, so this fires only once.)
      if (isDraftRef.current) pendingPersistRef.current = true

      const now = new Date().toISOString()
      const assistantId = `pending-${crypto.randomUUID()}`
      streamingIdRef.current = assistantId

      setMessages((prev) => [
        ...prev,
        { id: `user-${crypto.randomUUID()}`, role: 'user', content, created_at: now },
        { id: assistantId, role: 'assistant', content: '', created_at: now },
      ])
      setStatus('streaming')
      ws.send(JSON.stringify({ session_id: sessionId, content }))
    },
    [sessionId, status],
  )

  return { messages, status, connected, trace, send }
}
