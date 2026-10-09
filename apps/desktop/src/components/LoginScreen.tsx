import { login, restoreSession } from '../lib/auth'

interface Props {
  busy: boolean
  error: string | null
}

// Shown whenever there's no session. Sign-in opens Google in the system browser
// (the Electron main process runs the flow); this screen just waits for it.
export default function LoginScreen({ busy, error }: Props): JSX.Element {
  const available = Boolean(window.auth)

  return (
    <div className="login">
      <div className="login-card">
        <div className="login-logo">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="16 18 22 12 16 6" />
            <polyline points="8 6 2 12 8 18" />
          </svg>
          AgentIDE
        </div>
        <p className="login-sub">Sign in to get to your projects.</p>

        <button
          className="login-google"
          onClick={() => void login()}
          disabled={!available}
        >
          <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
            <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.6 13.2l7.9 6.2C12.4 13.6 17.7 9.5 24 9.5z" />
            <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.7 6c4.5-4.2 6.9-10.3 6.9-17.7z" />
            <path fill="#FBBC05" d="M10.5 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.2C1 16.4 0 20.1 0 24s1 7.6 2.6 10.8l7.9-6.2z" />
            <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.7-6c-2.2 1.5-5 2.3-8.2 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.2C6.6 42.6 14.6 48 24 48z" />
          </svg>
          {busy ? 'Restart sign-in' : 'Sign in with Google'}
        </button>

        {busy && (
          <p className="login-hint">Finish signing in in your browser, then come back here.</p>
        )}
        {error && <p className="login-error">{error}</p>}
        {error && available && !busy && (
          // e.g. the backend was down at launch: a saved session may still be valid.
          <button className="login-retry" onClick={() => void restoreSession()}>
            Retry saved session
          </button>
        )}
      </div>
    </div>
  )
}
