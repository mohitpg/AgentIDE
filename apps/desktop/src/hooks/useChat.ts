import { useCallback, useEffect, useRef, useState } from 'react'

import { getSession, WS_URL } from '../lib/api'
import type { Message, StreamEvent } from '../types'

type Status = 'idle' | 'streaming'

interface UseChat {
  messages: Message[]
  status: Status
  connected: boolean
  send: (content: string) => void
}

/**
 * Owns one WebSocket to /chat/ws and the message list for `sessionId`.
 * Token events stream into a placeholder assistant message; `done` finalizes it.
 */
export function useChat(sessionId: string | null): UseChat {
  const [messages, setMessages] = useState<Message[]>([])
  const [status, setStatus] = useState<Status>('idle')
  const [connected, setConnected] = useState(false)

  const wsRef = useRef<WebSocket | null>(null)
  const streamingIdRef = useRef<string | null>(null)

  // Load history whenever the active session changes.
  useEffect(() => {
    if (!sessionId) {
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

  const handleEvent = useCallback((event: StreamEvent): void => {
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
        break
      }
      case 'done': {
        streamingIdRef.current = null
        setStatus('idle')
        break
      }
      // agent_step / tool_* are surfaced in the Phase 2 trace panel.
      default:
        break
    }
  }, [])

  const send = useCallback(
    (content: string): void => {
      const ws = wsRef.current
      if (!sessionId || !content.trim() || status === 'streaming') return
      if (!ws || ws.readyState !== WebSocket.OPEN) return

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

  return { messages, status, connected, send }
}
