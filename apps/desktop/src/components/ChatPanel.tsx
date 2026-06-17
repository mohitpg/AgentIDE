import { useEffect, useRef, useState } from 'react'

import type { UseChat } from '../hooks/useChat'
import { uploadDocument } from '../lib/api'

interface Props {
  sessionId: string | null
  chat: UseChat
}

type UploadState =
  | { kind: 'idle' }
  | { kind: 'uploading'; filename: string }
  | { kind: 'done'; filename: string; chunks: number }
  | { kind: 'error'; message: string }

export default function ChatPanel({ sessionId, chat }: Props): JSX.Element {
  const { messages, status, connected, send } = chat
  const [input, setInput] = useState('')
  const [upload, setUpload] = useState<UploadState>({ kind: 'idle' })
  const scrollRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

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

  const onFileSelected = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = e.target.files?.[0]
    e.target.value = '' // allow re-selecting the same file
    if (!file) return
    setUpload({ kind: 'uploading', filename: file.name })
    try {
      const res = await uploadDocument(file, sessionId)
      setUpload({ kind: 'done', filename: res.filename, chunks: res.num_chunks })
    } catch (err) {
      setUpload({ kind: 'error', message: err instanceof Error ? err.message : 'Upload failed' })
    }
  }

  if (!sessionId) {
    return (
      <section className="chat empty-state">
        <p>Select a conversation or start a new chat.</p>
      </section>
    )
  }

  const uploading = upload.kind === 'uploading'

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

      {upload.kind !== 'idle' && (
        <div className={`upload-status ${upload.kind}`}>
          {upload.kind === 'uploading' && <span>Indexing {upload.filename}…</span>}
          {upload.kind === 'done' && (
            <span>✓ Indexed {upload.filename} — {upload.chunks} chunk{upload.chunks === 1 ? '' : 's'}</span>
          )}
          {upload.kind === 'error' && <span>⚠ {upload.message}</span>}
        </div>
      )}

      <div className="composer">
        <input
          ref={fileRef}
          type="file"
          accept=".txt,.md,.markdown,.pdf,.py,.js,.ts,.tsx,.json,.csv,.rst,.html,text/*,application/pdf"
          style={{ display: 'none' }}
          onChange={onFileSelected}
        />
        <button
          className="composer-upload"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          title="Upload a document to the knowledge base"
          aria-label="Upload document"
        >
          {uploading ? (
            '…'
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          )}
        </button>
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
