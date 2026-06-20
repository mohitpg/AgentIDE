import { useState } from 'react'

import ChatPanel from './components/ChatPanel'
import DocsPanel from './components/DocsPanel'
import HealthBadge from './components/HealthBadge'
import McpPanel from './components/McpPanel'
import SessionList from './components/SessionList'
import TasksPanel from './components/TasksPanel'
import TracePanel from './components/TracePanel'
import { useChat } from './hooks/useChat'
import { useDocuments } from './hooks/useDocuments'
import { useMcp } from './hooks/useMcp'
import { useTasks } from './hooks/useTasks'
import './App.css'

function App(): JSX.Element {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(true)
  const [showTrace, setShowTrace] = useState(true)
  const [showTasks, setShowTasks] = useState(false)
  const [showDocs, setShowDocs] = useState(false)
  const [showMcp, setShowMcp] = useState(false)
  // Filename of the source a citation points at; opens + highlights the Sources panel.
  const [highlightDoc, setHighlightDoc] = useState<string | null>(null)
  // One WebSocket for the session, shared by the chat and the trace panel.
  const chat = useChat(activeId)
  // Separate WebSocket for async task progress (runs independently of chat).
  const tasks = useTasks()
  // Indexed-document list backing the Sources panel.
  const documents = useDocuments()
  // Registered MCP servers backing the MCP settings panel.
  const mcp = useMcp()

  const openSource = (filename: string): void => {
    setHighlightDoc(filename)
    setShowDocs(true)
  }

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
          <button
            className={`panel-toggle ${showDocs ? 'on' : ''}`}
            onClick={() => setShowDocs((v) => !v)}
            title="Toggle indexed sources panel"
            aria-pressed={showDocs}
          >
            Sources
          </button>
          <button
            className={`panel-toggle ${showMcp ? 'on' : ''}`}
            onClick={() => setShowMcp((v) => !v)}
            title="Toggle MCP servers panel"
            aria-pressed={showMcp}
          >
            MCP
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
        <ChatPanel
          sessionId={activeId}
          chat={chat}
          onIndexed={documents.refresh}
          onOpenSource={openSource}
        />
        {showTrace && <TracePanel trace={chat.trace} onCollapse={() => setShowTrace(false)} />}
        {showTasks && <TasksPanel tasks={tasks} onCollapse={() => setShowTasks(false)} />}
        {showDocs && (
          <DocsPanel
            documents={documents}
            highlight={highlightDoc}
            onCollapse={() => setShowDocs(false)}
          />
        )}
        {showMcp && <McpPanel mcp={mcp} onCollapse={() => setShowMcp(false)} />}
      </main>
    </div>
  )
}

export default App
