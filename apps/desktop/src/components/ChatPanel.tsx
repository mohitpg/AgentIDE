import { useEffect, useRef, useState } from 'react'

import type { UseChat } from '../hooks/useChat'

interface Props {
  sessionId: string | null
  chat: UseChat
}

export default function ChatPanel({ sessionId, chat }: Props): JSX.Element {
  const { messages, status, connected, send } = chat
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  // Keep the latest message in view as tokens stream in.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  const submit = (): void => {
    if (!input.trim()) return
    send(input)
    setInput('')
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  if (!sessionId) {
    return (
      <section className="chat empty-state">
        <p>Select a conversation or start a new chat.</p>
      </section>
    )
  }

  return (
    <section className="chat">
      <div className="messages" ref={scrollRef}>
        {messages.map((m) => (
          <div key={m.id} className={`message ${m.role}`}>
            <div className="message-role">{m.role}</div>
            <div className="message-content">
              {m.content || (status === 'streaming' && m.role === 'assistant' ? '▍' : '')}
            </div>
          </div>
        ))}
      </div>

      <div className="composer">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={connected ? 'Send a message…  (Enter to send, Shift+Enter for newline)' : 'Connecting…'}
          rows={2}
          disabled={!connected}
        />
        <button onClick={submit} disabled={!connected || status === 'streaming' || !input.trim()}>
          {status === 'streaming' ? '…' : 'Send'}
        </button>
      </div>
    </section>
  )
}
