// Renderer-side session state: the in-memory access token, the signed-in user,
// and the active project — plus `authFetch`, which every REST call goes through.
//
// The Google flow and the refresh token live in the Electron main process
// (electron/auth.ts); this side only ever holds the short-lived access token. It
// refreshes shortly before expiry, and once more on any 401.

import { useSyncExternalStore } from 'react'

export interface AuthUser {
  id: string
  email: string
  display_name: string | null
  avatar_url: string | null
  is_superuser: boolean
}

export type AuthStatus = 'loading' | 'signedOut' | 'signedIn'

export interface AuthState {
  status: AuthStatus
  user: AuthUser | null
  error: string | null
  busy: boolean // a sign-in is in progress
}

interface AuthSession {
  accessToken: string
  expiresIn: number
  user: AuthUser
}

let state: AuthState = { status: 'loading', user: null, error: null, busy: false }
let accessToken: string | null = null
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let activeProjectId: string | null = null
const listeners = new Set<() => void>()

function setState(patch: Partial<AuthState>): void {
  state = { ...state, ...patch }
  listeners.forEach((l) => l())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribe, () => state)
}

// Electron wraps main-process errors as "Error invoking remote method 'x': Error: msg".
export function cleanError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err)
  return msg.replace(/^Error invoking remote method '[^']+': (Error: )?/, '')
}

function applySession(session: AuthSession): void {
  accessToken = session.accessToken
  clearTimeout(refreshTimer)
  // Refresh a minute before expiry so requests rarely hit a 401 at all.
  const ms = Math.max(30, session.expiresIn - 60) * 1000
  refreshTimer = setTimeout(() => void refreshAccessToken(), ms)
  setState({ status: 'signedIn', user: session.user, error: null, busy: false })
}

function signedOut(error: string | null = null): void {
  accessToken = null
  activeProjectId = null
  clearTimeout(refreshTimer)
  setState({ status: 'signedOut', user: null, error, busy: false })
}

// On launch: resume the persisted session, if the main process has one.
export async function restoreSession(): Promise<void> {
  if (!window.auth) {
    signedOut('Sign-in needs the AgentIDE desktop app.')
    return
  }
  try {
    const session = await window.auth.refresh()
    if (session) applySession(session)
    else signedOut()
  } catch (err) {
    signedOut(`Can't reach the backend: ${cleanError(err)}`)
  }
}

export async function login(): Promise<void> {
  if (!window.auth) return
  setState({ busy: true, error: null })
  try {
    applySession(await window.auth.login())
  } catch (err) {
    setState({ busy: false, error: cleanError(err) })
  }
}

export async function logout(): Promise<void> {
  try {
    await window.auth?.logout()
  } finally {
    signedOut()
  }
}

// Single-flight refresh shared by the timer, 401 retries, and socket reconnects.
let refreshing: Promise<string | null> | null = null
export function refreshAccessToken(): Promise<string | null> {
  if (!window.auth) return Promise.resolve(null)
  refreshing ??= window.auth
    .refresh()
    .then((session) => {
      if (!session) {
        signedOut('Your session has expired. Please sign in again.')
        return null
      }
      applySession(session)
      return session.accessToken
    })
    .catch(() => accessToken) // backend unreachable: keep the current token
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

export function getAccessToken(): string | null {
  return accessToken
}

// The project every scoped request runs in (sent as X-Project-Id / in the
// socket auth frame). Set by the project gate before the app's hooks mount.
export function setActiveProjectId(id: string | null): void {
  activeProjectId = id
}

export function getActiveProjectId(): string | null {
  return activeProjectId
}

function withAuth(init: RequestInit, token: string | null): RequestInit {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (activeProjectId) headers.set('X-Project-Id', activeProjectId)
  return { ...init, headers }
}

// fetch() with the bearer + project headers; on a 401, refresh once and retry.
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(url, withAuth(init, accessToken))
  if (res.status !== 401 || !window.auth) return res
  const fresh = await refreshAccessToken()
  if (!fresh) return res
  return fetch(url, withAuth(init, fresh))
}

// First frame on /chat/ws and /tasks/ws (browsers can't set WS headers, and a
// token in the URL would land in server logs).
export function socketAuthFrame(): string {
  return JSON.stringify({ type: 'auth', token: accessToken, project_id: activeProjectId })
}

// Close codes the gateway uses for a failed socket handshake (api/deps.py).
export const WS_UNAUTHORIZED = 4401
export const WS_FORBIDDEN = 4403
