import { useState } from 'react'

import ChatPanel from './components/ChatPanel'
import HealthBadge from './components/HealthBadge'
import SessionList from './components/SessionList'
import './App.css'

function App(): JSX.Element {
  const [activeId, setActiveId] = useState<string | null>(null)

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
        <HealthBadge />
      </header>

      <main className="app-body">
        <SessionList activeId={activeId} onSelect={(id) => setActiveId(id || null)} />
        <ChatPanel sessionId={activeId} />
      </main>
    </div>
  )
}

export default App
