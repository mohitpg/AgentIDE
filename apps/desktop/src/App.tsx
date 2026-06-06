import HealthBadge from './components/HealthBadge'
import './App.css'

function App(): JSX.Element {
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

      <main className="app-main">
        <div className="welcome">
          <h2>Phase 0 — Scaffolding complete</h2>
          <ul className="checklist">
            <li>✓ uv workspace + all Python packages</li>
            <li>✓ FastAPI gateway with /health</li>
            <li>✓ Postgres + pgvector · Redis · RabbitMQ</li>
            <li>✓ Alembic migrations (users table)</li>
            <li>✓ LangGraph orchestrator stub</li>
            <li>✓ A2A agent stubs (code, summarizer, vision, rag, web, indexer)</li>
            <li>✓ Celery workers (indexing, vision, web queues)</li>
            <li>✓ Electron + React desktop ← you are here</li>
          </ul>
          <p className="next-step">
            Next → <strong>Phase 1</strong>: single LangGraph agent, streaming chat
          </p>
        </div>
      </main>
    </div>
  )
}

export default App
