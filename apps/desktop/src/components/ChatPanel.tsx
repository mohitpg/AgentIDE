import { useEffect, useRef, useState } from 'react'

import type { UseChat } from '../hooks/useChat'
import { getTask, uploadDocumentAsync, uploadImage } from '../lib/api'
import { fileToDataUri, withImageRefs, type Attachment } from '../lib/images'
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

// A local-only unique id for an attachment chip (React key + in-place update).
// Not `crypto.randomUUID()` — that needs a secure context the renderer may lack.
let _attachmentSeq = 0
function newLocalId(): string {
  _attachmentSeq += 1
  return `att-${Date.now()}-${_attachmentSeq}`
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
  // Whether to keep the view pinned to the bottom as new tokens stream in. Stays
  // true while the user sits at the bottom; flips off the moment they scroll up,
  // and back on when they scroll back down. A ref (not state) so updating it
  // during a scroll event doesn't trigger a re-render.
  const stickToBottomRef = useRef(true)

  // Persist the thinking choice across reloads.
  useEffect(() => {
    localStorage.setItem(THINKING_KEY, thinking ? '1' : '0')
  }, [thinking])

  // Keep the latest message in view as tokens stream in — but only while the user
  // is parked at the bottom. If they've scrolled up to read, leave them be.
  useEffect(() => {
    if (!stickToBottomRef.current) return
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  // Track whether the user is at the bottom. Treat "within a few px" as the
  // bottom so the auto-scroll's own rounding doesn't read as a manual scroll-up.
  const onScroll = (e: React.UIEvent<HTMLDivElement>): void => {
    const el = e.currentTarget
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
  }

  const uploadingAttachments = attachments.some((a) => a.status === 'uploading')

  const submit = (): void => {
    // An image alone (no typed prompt) is a valid vision request.
    if (!input.trim() && attachments.length === 0) return
    if (!connected || status === 'streaming') return
    // Attachments upload the moment they're added; don't send until they settle.
    if (uploadingAttachments) return
    if (attachments.some((a) => a.status === 'error')) return // chips show the error

    // Embed each successfully-uploaded image's `<Image at db.images.{id}>` ref.
    const refs = attachments.flatMap((a) => (a.ref ? [a.ref] : []))
    send(withImageRefs(input, refs), thinking ? 'agent' : 'chat')
    setInput('')
    setAttachments([])
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  // Update one attachment in place once its async upload resolves.
  const patchAttachment = (localId: string, patch: Partial<Attachment>): void => {
    setAttachments((prev) => prev.map((a) => (a.localId === localId ? { ...a, ...patch } : a)))
  }

  // Turn image files (from paste, the file picker, or drop) into attachments, then
  // upload each to POST /images immediately so the chip can confirm it landed.
  const addImageFiles = async (files: Iterable<File>): Promise<void> => {
    const imgs = [...files].filter((f) => f.type.startsWith('image/'))
    const next: Attachment[] = await Promise.all(
      imgs.map(async (f) => ({
        localId: newLocalId(),
        file: f,
        dataUri: await fileToDataUri(f),
        name: f.name || 'pasted-image',
        status: 'uploading' as const,
      })),
    )
    if (next.length === 0) return
    setAttachments((prev) => [...prev, ...next])
    // Fire the uploads; reflect success (ref) or failure on each chip.
    for (const a of next) {
      void uploadImage(a.file, sessionId)
        .then((res) => patchAttachment(a.localId, { status: 'done', ref: res.ref }))
        .catch((err) =>
          patchAttachment(a.localId, {
            status: 'error',
            error: err instanceof Error ? err.message : 'Upload failed',
          }),
        )
    }
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
    // Copy the files out FIRST: `e.target.files` is a live FileList, and resetting
    // `value` (to allow re-selecting the same file) empties it — so reading it
    // after the reset would yield nothing.
    const files = e.target.files ? [...e.target.files] : []
    e.target.value = '' // allow re-selecting the same file
    if (files.length) void addImageFiles(files)
  }

  const removeAttachment = (localId: string): void => {
    setAttachments((prev) => prev.filter((a) => a.localId !== localId))
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
      <div className="messages" ref={scrollRef} onScroll={onScroll}>
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
            {attachments.map((a) => (
              <span
                className={`attachment-chip ${a.status}`}
                key={a.localId}
                title={a.status === 'error' ? a.error ?? 'Upload failed' : a.name}
              >
                <img className="attachment-thumb" src={a.dataUri} alt="" />
                <span className="attachment-name">{a.name}</span>
                <span className="attachment-status" aria-hidden>
                  {a.status === 'uploading' ? '…' : a.status === 'done' ? '✓' : '⚠'}
                </span>
                <button
                  type="button"
                  className="attachment-remove"
                  onClick={() => removeAttachment(a.localId)}
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
            onClick={() => submit()}
            disabled={
              !connected ||
              status === 'streaming' ||
              uploadingAttachments ||
              (!input.trim() && attachments.length === 0)
            }
          >
            {status === 'streaming' ? '…' : 'Send'}
          </button>
        </div>
      </div>
    </section>
  )
}
