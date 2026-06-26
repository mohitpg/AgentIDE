import { useState } from 'react'

import { imageUrl } from '../lib/api'
import { parseAnswer } from '../lib/citations'
import { splitImageRefs } from '../lib/images'
import type { AgentStep, Message } from '../types'
import Markdown from './Markdown'

interface Props {
  message: Message
  streaming: boolean
  onOpenSource?: (filename: string) => void
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
    // A user turn may carry attached images as `<Image at db.images.{id}>`
    // references — render each as a thumbnail (served by GET /images/{id})
    // rather than showing the raw reference token in the bubble.
    const { ids, text } = splitImageRefs(message.content)
    if (ids.length === 0) return <>{message.content}</>
    return (
      <>
        {text && <span className="message-text">{text}</span>}
        <div className="message-images">
          {ids.map((id, i) => (
            <img key={id} className="message-image" src={imageUrl(id)} alt={`attachment ${i + 1}`} />
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
  // Once content arrives, render it as markdown (plain text degrades gracefully).
  const bodyNode = !message.content ? (
    <>{caret && !stepsNode ? '▍' : ''}</>
  ) : (
    <>
      <div className="message-md">
        <Markdown content={body} streaming={caret} sources={sources} onOpenSource={onOpenSource} />
      </div>
      {sources.length > 0 && (
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
      )}
    </>
  )

  return (
    <>
      {stepsNode}
      {bodyNode}
    </>
  )
}
