import { useState } from 'react'

import ChatPanel from './components/ChatPanel'
import HealthBadge from './components/HealthBadge'
import SessionList from './components/SessionList'
import TasksPanel from './components/TasksPanel'
import TracePanel from './components/TracePanel'
import { useChat } from './hooks/useChat'
import { useTasks } from './hooks/useTasks'
import './App.css'

function App(): JSX.Element {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(true)
  const [showTrace, setShowTrace] = useState(true)
  const [showTasks, setShowTasks] = useState(false)
  // One WebSocket for the session, shared by the chat and the trace panel.
  const chat = useChat(activeId)
  // Separate WebSocket for async task progress (runs independently of chat).
  const tasks = useTasks()

  return (
    <div className="app">
      <header className="app-header">
        <span className="app-logo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="16 18 22 12 16 6" />
            <polyline points="8 6 2 12 8 18" />
          </svg>
          AgentIDE
        </span>
        <div className="app-header-right">
          <button
            className={`panel-toggle ${showHistory ? 'on' : ''}`}
            onClick={() => setShowHistory((v) => !v)}
            title="Toggle chat history"
            aria-pressed={showHistory}
          >
            History
          </button>
          <button
            className={`panel-toggle ${showTrace ? 'on' : ''}`}
            onClick={() => setShowTrace((v) => !v)}
            title="Toggle agent trace panel"
            aria-pressed={showTrace}
          >
            Trace
          </button>
          <button
            className={`panel-toggle ${showTasks ? 'on' : ''}`}
            onClick={() => setShowTasks((v) => !v)}
            title="Toggle async tasks panel"
            aria-pressed={showTasks}
          >
            Tasks
          </button>
          <HealthBadge />
        </div>
      </header>

      <main className="app-body">
        {showHistory && (
          <SessionList
            activeId={activeId}
            onSelect={(id) => setActiveId(id || null)}
            onCollapse={() => setShowHistory(false)}
          />
        )}
        <ChatPanel sessionId={activeId} chat={chat} />
        {showTrace && <TracePanel trace={chat.trace} onCollapse={() => setShowTrace(false)} />}
        {showTasks && <TasksPanel tasks={tasks} onCollapse={() => setShowTasks(false)} />}
      </main>
    </div>
  )
}

export default App
