import { useState } from 'react'

import type { UseTasks } from '../hooks/useTasks'
import { TASK_TERMINAL, type Task, type TaskStatus } from '../types'

interface Props {
  tasks: UseTasks
  onCollapse?: () => void
}

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: 'queued',
  started: 'running',
  progress: 'running',
  success: 'done',
  failure: 'failed',
  revoked: 'canceled',
}

function isActive(status: TaskStatus): boolean {
  return !TASK_TERMINAL.includes(status)
}

function preview(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function TaskCard({ task, onCancel }: { task: Task; onCancel: (id: string) => void }): JSX.Element {
  const active = isActive(task.status)
  return (
    <div className={`task-card ${task.status}`}>
      <div className="task-head">
        <span className="task-label">{task.label ?? task.type}</span>
        <span className={`task-status ${task.status}`}>{STATUS_LABEL[task.status]}</span>
      </div>

      <div className="task-progress">
        <div className="task-progress-track">
          <div
            className={`task-progress-fill ${task.status}`}
            style={{ width: `${Math.min(100, Math.max(0, task.progress))}%` }}
          />
        </div>
        <span className="task-progress-pct">{task.progress}%</span>
      </div>

      {task.message && <div className="task-message">{task.message}</div>}
      {task.error && <div className="task-error">{task.error}</div>}
      {task.status === 'success' && task.result && (
        <code className="task-result">{preview(task.result)}</code>
      )}

      <div className="task-foot">
        <span className="task-type">{task.type}</span>
        {active && (
          <button className="task-cancel" onClick={() => onCancel(task.id)}>
            Cancel
          </button>
        )}
      </div>
    </div>
  )
}

export default function TasksPanel({ tasks, onCollapse }: Props): JSX.Element {
  const [busy, setBusy] = useState(false)
  const { tasks: list, connected, enqueue, cancel } = tasks

  const runMock = async (): Promise<void> => {
    setBusy(true)
    try {
      await enqueue({ type: 'mock' })
    } catch {
      /* surfaced by the connection indicator */
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="tasks">
      <div className="tasks-header">
        <span className="tasks-title">
          Tasks
          <span
            className={`tasks-dot ${connected ? 'on' : 'off'}`}
            title={connected ? 'Live updates connected' : 'Disconnected'}
          />
        </span>
        <div className="tasks-header-actions">
          <button className="tasks-run" onClick={runMock} disabled={busy}>
            {busy ? 'Starting…' : '+ Mock task'}
          </button>
          {onCollapse && (
            <button
              className="panel-collapse"
              title="Collapse tasks"
              aria-label="Collapse tasks"
              onClick={onCollapse}
            >
              ›
            </button>
          )}
        </div>
      </div>

      <div className="tasks-body">
        {list.length === 0 ? (
          <div className="tasks-empty">
            No async tasks yet. Start a mock task to watch live progress stream while
            you keep chatting.
          </div>
        ) : (
          list.map((t) => <TaskCard key={t.id} task={t} onCancel={cancel} />)
        )}
      </div>
    </aside>
  )
}
