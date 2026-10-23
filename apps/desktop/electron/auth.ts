// Google sign-in for the desktop app (main process only).
//
// OAuth for installed apps (RFC 8252): open Google's consent page in the user's
// real browser with a PKCE challenge, catch the redirect on a one-shot loopback
// server (http://127.0.0.1:<random port>/), then hand the code + verifier to the
// gateway (`POST /auth/google`). The gateway holds the client secret, does the
// exchange, verifies Google's ID token, and returns our own token pair.
//
// The refresh token stays in this process: kept in memory and persisted with
// `safeStorage` (OS-level encryption: DPAPI / Keychain / libsecret). The renderer
// only ever receives the short-lived access token, so a compromised renderer
// can't mint new sessions.

import { app, safeStorage, shell } from 'electron'
import crypto from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'

// The backend, baked in at build time (vite.config.ts). An unpackaged (dev) app
// can point elsewhere with AGENTIDE_API_URL; installers always use their own.
export const API_BASE: string =
  (!app.isPackaged && process.env['AGENTIDE_API_URL']) || __AGENTIDE_API_URL__
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000

export interface AuthUser {
  id: string
  email: string
  display_name: string | null
  avatar_url: string | null
  is_superuser: boolean
}

// What the renderer gets: never the refresh token.
export interface AuthSession {
  accessToken: string
  expiresIn: number
  user: AuthUser
}

interface TokenResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  user: AuthUser
}

let refreshToken: string | null = null
// The latest access token, for main-process callers (the MCP bridge).
let access: { token: string; expiresAt: number } | null = null
let refreshInFlight: Promise<AuthSession | null> | null = null
let cancelPendingLogin: (() => void) | null = null

// ── refresh-token persistence ─────────────────────────────────────────────────
const sessionFile = (): string => path.join(app.getPath('userData'), 'session.bin')

function persist(token: string | null): void {
  refreshToken = token
  if (token === null) access = null
  try {
    if (token === null) {
      fs.rmSync(sessionFile(), { force: true })
    } else if (safeStorage.isEncryptionAvailable()) {
      fs.writeFileSync(sessionFile(), safeStorage.encryptString(token), { mode: 0o600 })
    }
    // No OS encryption available: keep it in memory only (sign in each launch)
    // rather than writing a live credential to disk in the clear.
  } catch (err) {
    console.warn('[auth] could not persist session:', err)
  }
}

function loadPersisted(): string | null {
  if (refreshToken) return refreshToken
  try {
    if (!safeStorage.isEncryptionAvailable()) return null
    const file = sessionFile()
    if (!fs.existsSync(file)) return null
    refreshToken = safeStorage.decryptString(fs.readFileSync(file))
    return refreshToken
  } catch {
    return null
  }
}

function adopt(tokens: TokenResponse): AuthSession {
  persist(tokens.refresh_token)
  access = { token: tokens.access_token, expiresAt: Date.now() + tokens.expires_in * 1000 }
  return { accessToken: tokens.access_token, expiresIn: tokens.expires_in, user: tokens.user }
}

async function errorDetail(res: Response): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
  return typeof body?.detail === 'string' ? body.detail : `${res.status} ${res.statusText}`
}

// ── PKCE + loopback ───────────────────────────────────────────────────────────
const b64url = (buf: Buffer): string => buf.toString('base64url')

const DONE_PAGE = (ok: boolean, msg: string): string =>
  `<!doctype html><meta charset="utf-8"><title>AgentIDE</title>
<body style="font-family:system-ui;background:#0f0f0f;color:#e5e5e5;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h2>${ok ? 'Signed in to AgentIDE' : 'Sign-in failed'}</h2>
<p style="color:#999">${msg}</p></div></body>`

// Start a loopback server, hand its redirect URI to `open`, and resolve with the
// authorization code once Google redirects back with a matching `state`.
function waitForCode(
  state: string,
  open: (redirectUri: string) => void,
): Promise<{ code: string; redirectUri: string }> {
  return new Promise((resolve, reject) => {
    let redirectUri = ''
    const server = http.createServer((req, res) => {
      const url = new URL(req.url ?? '/', 'http://127.0.0.1')
      const code = url.searchParams.get('code')
      const error = url.searchParams.get('error')
      if (!code && !error) {
        res.writeHead(404).end() // favicon etc.
        return
      }
      if (url.searchParams.get('state') !== state) {
        // Not our request (or forged); ignore it and keep waiting.
        res.writeHead(400, { 'Content-Type': 'text/html' }).end(DONE_PAGE(false, 'State mismatch.'))
        return
      }
      if (error || !code) {
        res.writeHead(200, { 'Content-Type': 'text/html' }).end(DONE_PAGE(false, 'You can close this tab.'))
        finish(new Error(error === 'access_denied' ? 'Sign-in was cancelled' : `Google returned: ${error}`))
        return
      }
      res.writeHead(200, { 'Content-Type': 'text/html' }).end(
        DONE_PAGE(true, 'You can close this tab and return to the app.'),
      )
      finish(null, code)
    })

    const timer = setTimeout(() => finish(new Error('Sign-in timed out')), LOGIN_TIMEOUT_MS)
    let settled = false
    function finish(err: Error | null, code?: string): void {
      if (settled) return
      settled = true
      clearTimeout(timer)
      cancelPendingLogin = null
      server.close()
      if (err) reject(err)
      else resolve({ code: code as string, redirectUri })
    }
    cancelPendingLogin = () => finish(new Error('Sign-in was restarted'))

    server.on('error', (err) => finish(err))
    // Port 0 = any free port; bound to loopback only so nothing else can reach it.
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as { port: number }
      redirectUri = `http://127.0.0.1:${port}/`
      open(redirectUri)
    })
  })
}

// ── public API (wired to ipcMain in main.ts) ──────────────────────────────────
export async function login(): Promise<AuthSession> {
  cancelPendingLogin?.() // a second click restarts the flow

  const cfgRes = await fetch(`${API_BASE}/auth/config`)
  if (!cfgRes.ok) throw new Error(`Backend unavailable (${cfgRes.status})`)
  const cfg = (await cfgRes.json()) as {
    google_enabled: boolean
    google_client_id: string
    authorization_endpoint: string
    scopes: string
  }
  if (!cfg.google_enabled) throw new Error('Google sign-in is not configured on the server')

  const verifier = b64url(crypto.randomBytes(32)) // 43 chars
  const challenge = b64url(crypto.createHash('sha256').update(verifier).digest())
  const state = b64url(crypto.randomBytes(16))

  const { code, redirectUri } = await waitForCode(state, (redirectUri) => {
    const url = new URL(cfg.authorization_endpoint)
    url.search = new URLSearchParams({
      client_id: cfg.google_client_id,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: cfg.scopes,
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
      prompt: 'select_account',
    }).toString()
    void shell.openExternal(url.toString())
  })

  const res = await fetch(`${API_BASE}/auth/google`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, code_verifier: verifier, redirect_uri: redirectUri }),
  })
  if (!res.ok) throw new Error(await errorDetail(res))
  return adopt((await res.json()) as TokenResponse)
}

// Exchange the stored refresh token for a fresh access token. Resolves null when
// the session is definitively over (no token, or the server rejected it); throws
// on network errors so a backend outage doesn't sign the user out.
// Single-flight: refresh tokens are single-use, so concurrent callers share one.
export function refresh(): Promise<AuthSession | null> {
  refreshInFlight ??= (async () => {
    const token = loadPersisted()
    if (!token) return null
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
    })
    if (res.status === 401) {
      persist(null)
      return null
    }
    if (!res.ok) throw new Error(await errorDetail(res))
    return adopt((await res.json()) as TokenResponse)
  })().finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

// A current access token for main-process callers (the MCP bridge): the cached
// one while it has a minute left, otherwise a refreshed one. Null once the
// session is over; throws on network errors, like `refresh`.
export async function accessToken(forceRefresh = false): Promise<string | null> {
  if (!forceRefresh && access && access.expiresAt - Date.now() > 60_000) return access.token
  const session = await refresh()
  return session?.accessToken ?? null
}

export async function logout(): Promise<void> {
  const token = loadPersisted()
  persist(null)
  if (!token) return
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
    })
  } catch {
    // Offline: the local session is gone either way; the token just expires.
  }
}
