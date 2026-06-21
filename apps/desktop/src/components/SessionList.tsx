import { useEffect, useState } from 'react'

import { deleteSession, listSessions } from '../lib/api'
import type { Session } from '../types'

interface Props {
  activeId: string | null
  onSelect: (id: string) => void
  // Open a new (draft) chat. The session is created in the DB lazily, on its
  // first message, so this never writes an empty conversation.
  onNewChat: () => void
  // Bump to re-fetch the list (e.g. after a draft is persisted).
  refreshSignal?: number
  onCollapse?: () => void
}

export default function SessionList({
  activeId,
  onSelect,
  onNewChat,
  refreshSignal,
  onCollapse,
}: Props): JSX.Element {
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

  // Re-fetch when a draft is persisted (and when the active session changes,
  // to pull in title/order updates).
  useEffect(() => {
    void refresh()
  }, [activeId, refreshSignal])

  const handleDelete = async (e: React.MouseEvent, id: string): Promise<void> => {
    e.stopPropagation()
    await deleteSession(id)
    await refresh()
    if (activeId === id) onSelect('')
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <span className="sidebar-title">Chats</span>
        {onCollapse && (
          <button
            className="panel-collapse"
            title="Collapse chat history"
            aria-label="Collapse chat history"
            onClick={onCollapse}
          >
            ‹
          </button>
        )}
      </div>
      <button className="new-chat" onClick={onNewChat}>
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
