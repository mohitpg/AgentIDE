import { useEffect, useRef, useState } from 'react'

import type { UseChat } from '../hooks/useChat'
import { getTask, uploadDocumentAsync } from '../lib/api'
import { fileToDataUri, withImages, type Attachment } from '../lib/images'
import type { Task } from '../types'
import { TASK_TERMINAL } from '../types'
import Dropdown from './Dropdown'
import MessageContent from './MessageContent'
import Toggle from './Toggle'

const IMAGE_ACCEPT = 'image/png,image/jpeg,image/gif,image/webp,image/bmp'
const DOC_ACCEPT =
  '.txt,.md,.markdown,.pdf,.py,.js,.ts,.tsx,.json,.csv,.rst,.html,text/*,application/pdf'

// Composer "thinking" mode. On → multi-agent ("agent"); off → plain chat for a
// fast response (no "auto"). Persisted so the choice survives reloads.
const THINKING_KEY = 'agentide.thinking'

function loadThinking(): boolean {
  // Default on (agent mode); only an explicit opt-out disables it.
  return localStorage.getItem(THINKING_KEY) !== '0'
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
  const [thinking, setThinking] = useState<boolean>(loadThinking)
  const [upload, setUpload] = useState<UploadState>({ kind: 'idle' })
  // Images attached to the next message (sent inline to the vision agent).
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const docRef = useRef<HTMLInputElement>(null)
  const imageRef = useRef<HTMLInputElement>(null)

  // Persist the thinking choice across reloads.
  useEffect(() => {
    localStorage.setItem(THINKING_KEY, thinking ? '1' : '0')
  }, [thinking])

  // Keep the latest message in view as tokens stream in.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  const submit = (): void => {
    // An image alone (no typed prompt) is a valid vision request.
    if (!input.trim() && attachments.length === 0) return
    send(withImages(input, attachments), thinking ? 'agent' : 'chat')
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
          <Dropdown
            className="thinking-dropdown"
            menuClassName="thinking-menu"
            menuRole="dialog"
            menuLabel="Response mode"
            trigger={({ open, toggle }) => (
              <button
                type="button"
                className={`thinking-chip${thinking ? ' on' : ''}`}
                onClick={toggle}
                title="Response mode — thinking (multi-agent) or fast chat"
                aria-haspopup="dialog"
                aria-expanded={open}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z" />
                </svg>
                <span className="thinking-chip-label">{thinking ? 'Thinking' : 'Fast'}</span>
              </button>
            )}
          >
            <div className="thinking-row">
              <div className="thinking-row-text">
                <span className="thinking-row-title">Thinking</span>
                <span className="thinking-row-hint">
                  {thinking
                    ? 'Multi-agent — uses specialists'
                    : 'Plain chat — fast response'}
                </span>
              </div>
              <Toggle checked={thinking} onChange={setThinking} label="Toggle thinking mode" />
            </div>
          </Dropdown>
          <Dropdown
            className="attach"
            menuClassName="attach-menu"
            menuLabel="Attach"
            trigger={({ open, toggle }) => (
              <button
                type="button"
                className="composer-upload"
                onClick={toggle}
                disabled={uploading}
                title="Attach an image or document"
                aria-label="Attach"
                aria-haspopup="menu"
                aria-expanded={open}
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
            )}
          >
            {({ close }) => (
              <>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    close()
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
                    close()
                    docRef.current?.click()
                  }}
                >
                  Upload document
                  <span className="attach-menu-hint">index into the knowledge base (RAG)</span>
                </button>
              </>
            )}
          </Dropdown>
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
