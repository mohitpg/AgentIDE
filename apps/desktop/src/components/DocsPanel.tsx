import { useEffect, useRef, type Ref } from 'react'

import type { UseDocuments } from '../hooks/useDocuments'
import type { RagDocument } from '../types'

interface Props {
  documents: UseDocuments
  // Filename of a source the user clicked in a chat citation — highlight + scroll to it.
  highlight?: string | null
  onCollapse?: () => void
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function DocCard({
  doc,
  highlighted,
  onDelete,
  cardRef,
}: {
  doc: RagDocument
  highlighted: boolean
  onDelete: (id: string) => void
  cardRef?: Ref<HTMLDivElement>
}): JSX.Element {
  return (
    <div ref={cardRef} className={`doc-card ${highlighted ? 'highlight' : ''}`}>
      <div className="doc-head">
        <span className="doc-name" title={doc.filename}>
          {doc.filename}
        </span>
        <button className="doc-delete" title="Delete document" onClick={() => onDelete(doc.id)}>
          ×
        </button>
      </div>
      <div className="doc-meta">
        <span>
          {doc.num_chunks} chunk{doc.num_chunks === 1 ? '' : 's'}
        </span>
        {doc.byte_size != null && <span>· {formatBytes(doc.byte_size)}</span>}
        <span>· {new Date(doc.created_at).toLocaleDateString()}</span>
      </div>
    </div>
  )
}

export default function DocsPanel({ documents, highlight, onCollapse }: Props): JSX.Element {
  const { documents: list, refresh, remove } = documents
  const highlightRef = useRef<HTMLDivElement>(null)

  // Scroll the highlighted source into view when a citation is clicked.
  useEffect(() => {
    if (highlight) highlightRef.current?.scrollIntoView({ block: 'nearest' })
  }, [highlight, list])

  const onDelete = (id: string): void => {
    void remove(id).catch(() => {
      /* row stays; user can retry */
    })
  }

  // Highlight only the first document matching the clicked filename.
  const highlightId = highlight ? list.find((d) => d.filename === highlight)?.id : undefined

  return (
    <aside className="docs">
      <div className="docs-header">
        <span className="docs-title">Sources</span>
        <div className="docs-header-actions">
          <button className="docs-refresh" title="Refresh" aria-label="Refresh documents" onClick={refresh}>
            ⟳
          </button>
          {onCollapse && (
            <button
              className="panel-collapse"
              title="Collapse sources"
              aria-label="Collapse sources"
              onClick={onCollapse}
            >
              ›
            </button>
          )}
        </div>
      </div>

      <div className="docs-body">
        {list.length === 0 ? (
          <div className="docs-empty">
            No documents indexed yet. Use the 📎 in the composer to add one, then ask a
            question grounded in it.
          </div>
        ) : (
          list.map((d) => (
            <DocCard
              key={d.id}
              doc={d}
              highlighted={d.id === highlightId}
              onDelete={onDelete}
              cardRef={d.id === highlightId ? highlightRef : undefined}
            />
          ))
        )}
      </div>
    </aside>
  )
}
