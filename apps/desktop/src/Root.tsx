import { useEffect } from 'react'

import App from './App'
import LoginScreen from './components/LoginScreen'
import { useProjects } from './hooks/useProjects'
import { restoreSession, setActiveProjectId, useAuth, type AuthUser } from './lib/auth'

// Auth gate: resume the saved session on launch, otherwise show sign-in.
export default function Root(): JSX.Element {
  const auth = useAuth()

  useEffect(() => {
    void restoreSession()
  }, [])

  if (auth.status === 'loading') return <div className="splash">Loading…</div>
  if (auth.status === 'signedOut' || !auth.user) {
    return <LoginScreen busy={auth.busy} error={auth.error} />
  }
  // Keyed on the user so signing in as someone else starts from scratch.
  return <ProjectGate key={auth.user.id} user={auth.user} />
}

// Project gate: nothing user-scoped renders until a project is chosen.
function ProjectGate({ user }: { user: AuthUser }): JSX.Element {
  const projects = useProjects(user)

  if (!projects.activeId) {
    return (
      <div className="splash">
        {projects.error ? (
          <>
            <span>Couldn't load your projects: {projects.error}</span>
            <button className="new-chat" onClick={projects.reload}>
              Retry
            </button>
          </>
        ) : (
          'Loading projects…'
        )}
      </div>
    )
  }

  // Set synchronously, before App mounts: children's effects run before any
  // effect here, and their first requests must already carry this project.
  setActiveProjectId(projects.activeId)
  return <App key={projects.activeId} user={user} projects={projects} />
}
