import { useState, type ReactNode } from 'react'

import { parseAnswer, type Source } from '../lib/citations'
import { splitImages } from '../lib/images'
import type { AgentStep, Message } from '../types'

interface Props {
  message: Message
  streaming: boolean
  onOpenSource?: (filename: string) => void
}

// Render the body, turning inline [n] markers into clickable citation badges that
// jump to the matching source (when that n is in the footer).
function renderBody(
  body: string,
  sources: Source[],
  onOpenSource?: (filename: string) => void,
): ReactNode[] {
  return body.split(/(\[\d+\])/g).map((part, i) => {
    const m = /^\[(\d+)\]$/.exec(part)
    const src = m ? sources.find((s) => s.n === Number(m[1])) : undefined
    if (m && src) {
      return (
        <button
          key={i}
          className="citation-ref"
          title={`${src.filename} · chunk ${src.chunk}`}
          onClick={() => onOpenSource?.(src.filename)}
        >
          {src.n}
        </button>
      )
    }
    return <span key={i}>{part}</span>
  })
}

// One intermediate specialist step, collapsed by default. While the step is still
// running it shows a live dot; click the header to reveal its streamed output.
function ThinkingStep({ step }: { step: AgentStep }): JSX.Element {
  const [open, setOpen] = useState(false)
  const label = step.skill ? `${step.agent} · ${step.skill}` : step.agent
  return (
    <div className={`thinking ${step.done ? 'done' : 'active'}`}>
      <button className="thinking-head" onClick={() => setOpen((o) => !o)}>
        <span className="thinking-chevron">{open ? '▾' : '▸'}</span>
        <span className="thinking-label">Thinking — {label}</span>
        {step.done ? (
          <span className="thinking-check" aria-hidden>
            ✓
          </span>
        ) : (
          <span className="thinking-dot" aria-hidden />
        )}
      </button>
      {open && <div className="thinking-body">{step.content || '…'}</div>}
    </div>
  )
}

export default function MessageContent({ message, streaming, onOpenSource }: Props): JSX.Element {
  const caret = streaming && message.role === 'assistant'

  if (message.role !== 'assistant') {
    // A user turn may carry pasted/attached images inline as data URIs — render
    // them as thumbnails rather than dumping the raw base64 into the bubble.
    const { images, text } = splitImages(message.content)
    if (images.length === 0) return <>{message.content}</>
    return (
      <>
        {text && <span className="message-text">{text}</span>}
        <div className="message-images">
          {images.map((src, i) => (
            <img key={i} className="message-image" src={src} alt={`attachment ${i + 1}`} />
          ))}
        </div>
      </>
    )
  }

  const steps = message.steps ?? []
  const stepsNode =
    steps.length > 0 ? (
      <div className="thinking-steps">
        {steps.map((s, i) => (
          <ThinkingStep key={i} step={s} />
        ))}
      </div>
    ) : null

  const { body, sources } = parseAnswer(message.content)

  // Show the typing caret only while the final answer itself is still empty and
  // no step is carrying the work (avoids a stray caret under the Thinking blocks).
  const bodyNode =
    sources.length === 0 ? (
      <>{message.content || (caret && !stepsNode ? '▍' : '')}</>
    ) : (
      <>
        <span className="message-text">{renderBody(body, sources, onOpenSource)}</span>
        <div className="citations">
          {sources.map((s) => (
            <button
              key={s.n}
              className="citation-chip"
              title={`Open ${s.filename}`}
              onClick={() => onOpenSource?.(s.filename)}
            >
              <span className="citation-chip-n">{s.n}</span>
              <span className="citation-chip-file">{s.filename}</span>
              <span className="citation-chip-chunk">#{s.chunk}</span>
            </button>
          ))}
        </div>
      </>
    )

  return (
    <>
      {stepsNode}
      {bodyNode}
    </>
  )
}
