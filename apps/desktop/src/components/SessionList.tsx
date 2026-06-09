import { useEffect, useState } from 'react'

import { createSession, deleteSession, listSessions } from '../lib/api'
import type { Session } from '../types'

interface Props {
  activeId: string | null
  onSelect: (id: string) => void
}

export default function SessionList({ activeId, onSelect }: Props): JSX.Element {
  const [sessions, setSessions] = useState<Session[]>([])

  const refresh = async (): Promise<void> => {
    try {
      setSessions(await listSessions())
    } catch {
      setSessions([])
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  // Pull in title/order updates after a turn changes the active session.
  useEffect(() => {
    void refresh()
  }, [activeId])

  const handleNew = async (): Promise<void> => {
    const session = await createSession()
    await refresh()
    onSelect(session.id)
  }

  const handleDelete = async (e: React.MouseEvent, id: string): Promise<void> => {
    e.stopPropagation()
    await deleteSession(id)
    await refresh()
    if (activeId === id) onSelect('')
  }

  return (
    <aside className="sidebar">
      <button className="new-chat" onClick={() => void handleNew()}>
        + New chat
      </button>
      <ul className="session-list">
        {sessions.map((s) => (
          <li
            key={s.id}
            className={`session-item ${s.id === activeId ? 'active' : ''}`}
            onClick={() => onSelect(s.id)}
          >
            <span className="session-title">{s.title ?? 'New chat'}</span>
            <button
              className="session-delete"
              title="Delete"
              onClick={(e) => void handleDelete(e, s.id)}
            >
              ×
            </button>
          </li>
        ))}
        {sessions.length === 0 && <li className="session-empty">No conversations yet</li>}
      </ul>
    </aside>
  )
}
