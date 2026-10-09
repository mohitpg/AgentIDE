import { useState } from 'react'

import type { UseProjects } from '../hooks/useProjects'
import Dropdown from './Dropdown'

// Header chip for the active project. Each project is an isolated space: its own
// chats, tasks, uploads, and indexed documents. Switching remounts the app.
export default function ProjectSwitcher({ projects }: { projects: UseProjects }): JSX.Element {
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const active = projects.projects.find((p) => p.id === projects.activeId)

  const onCreate = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setError(null)
    try {
      await projects.create(trimmed) // also switches to it
      setName('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create project')
    }
  }

  const onDelete = async (id: string, label: string): Promise<void> => {
    const ok = window.confirm(
      `Delete "${label}"? Its chats, tasks, uploads and indexed documents are deleted too.`,
    )
    if (!ok) return
    setError(null)
    try {
      await projects.remove(id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete project')
    }
  }

  return (
    <Dropdown
      menuClassName="dropdown-menu-down project-menu"
      menuLabel="Projects"
      trigger={({ open, toggle }) => (
        <button
          className="workspace-chip"
          onClick={toggle}
          aria-expanded={open}
          title="Switch project"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
          <span className="workspace-chip-name">{active?.name ?? '…'}</span>
          <span className="chip-caret" aria-hidden>▾</span>
        </button>
      )}
    >
      {({ close }) => (
        <>
          <div className="menu-label">Projects</div>
          {projects.projects.map((p) => (
            <div key={p.id} className={`project-item ${p.id === projects.activeId ? 'active' : ''}`}>
              <button
                className="project-item-name"
                onClick={() => {
                  close()
                  if (p.id !== projects.activeId) projects.select(p.id)
                }}
              >
                {p.name}
              </button>
              {projects.projects.length > 1 && (
                <button
                  className="project-item-delete"
                  title={`Delete ${p.name}`}
                  aria-label={`Delete ${p.name}`}
                  onClick={() => void onDelete(p.id, p.name)}
                >
                  ×
                </button>
              )}
            </div>
          ))}
          <form className="project-new" onSubmit={(e) => void onCreate(e)}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="New project…"
              maxLength={255}
              aria-label="New project name"
            />
            <button type="submit" disabled={!name.trim()}>
              Add
            </button>
          </form>
          {error && <div className="menu-error">{error}</div>}
        </>
      )}
    </Dropdown>
  )
}
