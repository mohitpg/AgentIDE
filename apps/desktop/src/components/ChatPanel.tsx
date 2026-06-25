import { useEffect, useRef, useState } from 'react'

import type { UseChat } from '../hooks/useChat'
import { getTask, uploadDocumentAsync } from '../lib/api'
import { fileToDataUri, withImages, type Attachment } from '../lib/images'
import type { ChatMode, Task } from '../types'
import { TASK_TERMINAL } from '../types'
import MessageContent from './MessageContent'

const IMAGE_ACCEPT = 'image/png,image/jpeg,image/gif,image/webp,image/bmp'
const DOC_ACCEPT =
  '.txt,.md,.markdown,.pdf,.py,.js,.ts,.tsx,.json,.csv,.rst,.html,text/*,application/pdf'

// Composer execution-mode toggle. Persisted so the choice survives reloads.
const MODE_KEY = 'agentide.chatMode'
const MODES: { value: ChatMode; label: string; title: string }[] = [
  { value: 'auto', label: 'Auto', title: 'Supervisor decides when to use specialists' },
  { value: 'agent', label: 'Agent', title: 'Prefer multi-agent specialists' },
  { value: 'chat', label: 'Chat', title: 'Plain chat — never delegates' },
]

function loadMode(): ChatMode {
  const saved = localStorage.getItem(MODE_KEY)
  return saved === 'agent' || saved === 'chat' ? saved : 'auto'
}

interface Props {
  sessionId: string | null
  chat: UseChat
  // Called when an upload finishes indexing, so the Sources panel can refresh.
  onIndexed?: () => void
  // Called when a citation in an answer is clicked (filename of the source).
  onOpenSource?: (filename: string) => void
}

type UploadState =
  | { kind: 'idle' }
  | { kind: 'indexing'; filename: string; progress: number; message: string }
  | { kind: 'done'; filename: string; chunks: number }
  | { kind: 'error'; message: string }

// Poll a task to completion, reporting each tick. The first index also downloads
// the embedding model, so allow a generous timeout.
async function pollTask(id: string, onTick: (t: Task) => void): Promise<Task> {
  const deadline = Date.now() + 300_000
  for (;;) {
    const task = await getTask(id)
    onTick(task)
    if (TASK_TERMINAL.includes(task.status)) return task
    if (Date.now() > deadline) throw new Error('Indexing timed out')
    await new Promise((r) => setTimeout(r, 1200))
  }
}

export default function ChatPanel({ sessionId, chat, onIndexed, onOpenSource }: Props): JSX.Element {
  const { messages, status, connected, send } = chat
  const [input, setInput] = useState('')
  const [mode, setMode] = useState<ChatMode>(loadMode)
  const [upload, setUpload] = useState<UploadState>({ kind: 'idle' })
  // Images attached to the next message (sent inline to the vision agent).
  const [attachments, setAttachments] = useState<Attachment[]>([])
  // The composer "+" attach menu (dropup) open state.
  const [menuOpen, setMenuOpen] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const docRef = useRef<HTMLInputElement>(null)
  const imageRef = useRef<HTMLInputElement>(null)
  const attachWrapRef = useRef<HTMLDivElement>(null)

  // Persist the mode choice across reloads.
  useEffect(() => {
    localStorage.setItem(MODE_KEY, mode)
  }, [mode])

  // Keep the latest message in view as tokens stream in.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  // Close the attach menu on an outside click.
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent): void => {
      if (!attachWrapRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])

  const submit = (): void => {
    // An image alone (no typed prompt) is a valid vision request.
    if (!input.trim() && attachments.length === 0) return
    send(withImages(input, attachments), mode)
    setInput('')
    setAttachments([])
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  // Turn image files (from paste, the file picker, or drop) into inline attachments.
  const addImageFiles = async (files: Iterable<File>): Promise<void> => {
    const imgs = [...files].filter((f) => f.type.startsWith('image/'))
    const next = await Promise.all(
      imgs.map(async (f) => ({ dataUri: await fileToDataUri(f), name: f.name || 'pasted-image' })),
    )
    if (next.length) setAttachments((prev) => [...prev, ...next])
  }

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>): void => {
    const files = [...e.clipboardData.items]
      .filter((it) => it.kind === 'file' && it.type.startsWith('image/'))
      .map((it) => it.getAsFile())
      .filter((f): f is File => f !== null)
    if (files.length === 0) return // let normal text paste through
    e.preventDefault()
    void addImageFiles(files)
  }

  const onImageSelected = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const files = e.target.files
    e.target.value = '' // allow re-selecting the same file
    if (files) void addImageFiles(files)
  }

  const removeAttachment = (i: number): void => {
    setAttachments((prev) => prev.filter((_, idx) => idx !== i))
  }

  const onFileSelected = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = e.target.files?.[0]
    e.target.value = '' // allow re-selecting the same file
    if (!file) return
    setUpload({ kind: 'indexing', filename: file.name, progress: 0, message: 'Queued' })
    try {
      const task = await uploadDocumentAsync(file, sessionId)
      const final = await pollTask(task.id, (t) =>
        setUpload({
          kind: 'indexing',
          filename: file.name,
          progress: t.progress,
          message: t.message ?? t.status,
        }),
      )
      if (final.status === 'success') {
        const chunks = Number(final.result?.num_chunks ?? 0)
        setUpload({ kind: 'done', filename: file.name, chunks })
        onIndexed?.()
      } else {
        setUpload({ kind: 'error', message: final.error ?? `Indexing ${final.status}` })
      }
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

  const uploading = upload.kind === 'indexing'

  return (
    <section className="chat">
      <div className="messages" ref={scrollRef}>
        {messages.map((m) => (
          <div key={m.id} className={`message ${m.role}`}>
            <div className="message-role">{m.role}</div>
            <div className="message-content">
              <MessageContent
                message={m}
                streaming={status === 'streaming'}
                onOpenSource={onOpenSource}
              />
            </div>
          </div>
        ))}
      </div>

      {upload.kind !== 'idle' && (
        <div className={`upload-status ${upload.kind}`}>
          <div className="upload-status-inner">
            {upload.kind === 'indexing' && (
              <span>
                Indexing {upload.filename}… {upload.progress}% · {upload.message}
              </span>
            )}
            {upload.kind === 'done' && (
              <span>✓ Indexed {upload.filename} — {upload.chunks} chunk{upload.chunks === 1 ? '' : 's'}</span>
            )}
            {upload.kind === 'error' && <span>⚠ {upload.message}</span>}
          </div>
        </div>
      )}

      <div className="composer">
        <input
          ref={docRef}
          type="file"
          accept={DOC_ACCEPT}
          style={{ display: 'none' }}
          onChange={onFileSelected}
        />
        <input
          ref={imageRef}
          type="file"
          accept={IMAGE_ACCEPT}
          multiple
          style={{ display: 'none' }}
          onChange={onImageSelected}
        />
        <div className="mode-toggle" role="group" aria-label="Chat mode">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              className={`mode-option ${mode === m.value ? 'active' : ''}`}
              title={m.title}
              aria-pressed={mode === m.value}
              onClick={() => setMode(m.value)}
            >
              {m.label}
            </button>
          ))}
        </div>
        {attachments.length > 0 && (
          <div className="attachments">
            {attachments.map((a, i) => (
              <span className="attachment-chip" key={i} title={a.name}>
                <img className="attachment-thumb" src={a.dataUri} alt="" />
                <span className="attachment-name">{a.name}</span>
                <button
                  type="button"
                  className="attachment-remove"
                  onClick={() => removeAttachment(i)}
                  aria-label={`Remove ${a.name}`}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="composer-inner">
          <div className="attach" ref={attachWrapRef}>
            <button
              type="button"
              className="composer-upload"
              onClick={() => setMenuOpen((o) => !o)}
              disabled={uploading}
              title="Attach an image or document"
              aria-label="Attach"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
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
            {menuOpen && (
              <div className="attach-menu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false)
                    imageRef.current?.click()
                  }}
                >
                  Attach image
                  <span className="attach-menu-hint">for the vision agent — analyse / OCR</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false)
                    docRef.current?.click()
                  }}
                >
                  Upload document
                  <span className="attach-menu-hint">index into the knowledge base (RAG)</span>
                </button>
              </div>
            )}
          </div>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            placeholder={connected ? 'Send a message…  (Enter to send, Shift+Enter for newline)' : 'Connecting…'}
            rows={2}
            disabled={!connected}
          />
          <button
            onClick={submit}
            disabled={!connected || status === 'streaming' || (!input.trim() && attachments.length === 0)}
          >
            {status === 'streaming' ? '…' : 'Send'}
          </button>
        </div>
      </div>
    </section>
  )
}
