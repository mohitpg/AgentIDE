// The desktop half of the MCP bridge: a WebSocket to the backend that lends the
// local MCP servers (host.ts) to the signed-in user's cloud agents.
//
// After sign-in it connects to `${API}/mcp/bridge/ws`, authenticates with the
// first frame, and pushes the inventory: server ids, exposure and tool schemas,
// never commands. The backend then relays agents' `call` frames; each runs on
// the local server, after the user approves it if the tool can change things,
// and the result goes back as a `result` frame. The socket reconnects with
// backoff, except when a newer connection for the same account takes over
// (close code 4409): then it waits until the user asks to use this device.

import { needsConfirm, type McpHost } from './host'
import type { BridgeStatus } from './types'

const WS_UNAUTHORIZED = 4401
const WS_REPLACED = 4409
const PING_MS = 25_000
const MAX_BACKOFF_MS = 30_000

export interface ConfirmRequest {
  agentId: string
  serverName: string
  tool: string
  args: Record<string, unknown>
}

export type ConfirmDecision = 'allow' | 'always' | 'deny'

interface Options {
  apiBase: string
  // A current access token (refreshed when stale or when forced); null once signed out.
  getToken: (forceRefresh?: boolean) => Promise<string | null>
  host: McpHost
  confirm: (request: ConfirmRequest, signal: AbortSignal) => Promise<ConfirmDecision>
  onChange: () => void
}

interface CallFrame {
  type: 'call'
  id: string
  agent_id: string
  server_id: string
  tool: string
  arguments?: Record<string, unknown>
}

const message = (err: unknown): string => (err instanceof Error ? err.message : String(err))

export class McpBridge {
  private ws: WebSocket | null = null
  private running = false
  private epoch = 0 // bumped on every connect/teardown; stale callbacks back off
  private status: BridgeStatus = 'signedOut'
  private detail: string | null = null
  private attempt = 0
  private forceRefresh = false
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined
  private pushTimer: ReturnType<typeof setTimeout> | undefined
  private pingTimer: ReturnType<typeof setInterval> | undefined
  private lastInventory = ''
  private readonly inflight = new Map<string, AbortController>()
  private readonly alwaysAllowed = new Set<string>()
  private confirmQueue: Promise<unknown> = Promise.resolve()

  constructor(private readonly opts: Options) {}

  get state(): { status: BridgeStatus; detail: string | null } {
    return { status: this.status, detail: this.detail }
  }

  // Called after every successful sign-in or token refresh; connects once.
  start(): void {
    if (this.running) return
    this.running = true
    this.attempt = 0
    void this.connect()
  }

  // Sign-out: disconnect, stop retrying, and forget "always allow" approvals.
  stop(): void {
    this.running = false
    this.alwaysAllowed.clear()
    this.teardown()
    this.setStatus('signedOut', null)
  }

  // The user asked to use this device's tools (e.g. after another window took over).
  reconnect(): void {
    if (!this.running) {
      this.start()
      return
    }
    this.attempt = 0
    this.teardown()
    void this.connect()
  }

  // The local servers changed; tell the backend (coalesced, and only real changes).
  pushInventory(): void {
    clearTimeout(this.pushTimer)
    this.pushTimer = setTimeout(() => this.sendInventory(), 150)
  }

  // ── connection ──────────────────────────────────────────────────────────────
  private async connect(): Promise<void> {
    clearTimeout(this.reconnectTimer)
    const epoch = ++this.epoch
    this.setStatus('connecting', null)

    let token: string | null
    try {
      token = await this.opts.getToken(this.forceRefresh)
      this.forceRefresh = false
    } catch (err) {
      if (epoch === this.epoch) this.retry(`Can't reach the backend: ${message(err)}`)
      return
    }
    if (epoch !== this.epoch || !this.running) return
    if (!token) {
      this.running = false
      this.setStatus('signedOut', null)
      return
    }

    const ws = new WebSocket(`${this.opts.apiBase.replace(/^http/, 'ws')}/mcp/bridge/ws`)
    this.ws = ws
    this.lastInventory = ''
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }))
    ws.onmessage = (event: MessageEvent) => this.onMessage(ws, event.data)
    ws.onclose = (event: CloseEvent) => this.onClose(ws, event.code, event.reason)
  }

  private onMessage(ws: WebSocket, data: unknown): void {
    if (ws !== this.ws) return
    let frame: { type?: string; id?: string; message?: string }
    try {
      frame = JSON.parse(String(data))
    } catch {
      return
    }
    switch (frame.type) {
      case 'ready':
        this.attempt = 0
        this.setStatus('connected', null)
        this.sendInventory()
        clearInterval(this.pingTimer)
        this.pingTimer = setInterval(() => this.send(ws, { type: 'ping' }), PING_MS)
        break
      case 'call':
        void this.handleCall(ws, frame as CallFrame)
        break
      case 'cancel':
        if (frame.id) this.inflight.get(frame.id)?.abort()
        break
      case 'error':
        console.warn('[mcp-bridge] backend says:', frame.message)
        break
    }
  }

  private onClose(ws: WebSocket, code: number, reason: string): void {
    if (ws !== this.ws) return
    this.ws = null
    clearInterval(this.pingTimer)
    this.abortInflight()
    if (!this.running) return
    if (code === WS_REPLACED) {
      this.setStatus(
        'replaced',
        'Agents are using the tools of another AgentIDE window or device on this account.',
      )
      return
    }
    if (code === WS_UNAUTHORIZED) this.forceRefresh = true
    this.retry(reason || 'Connection lost')
  }

  private retry(detail: string): void {
    if (!this.running) return
    this.setStatus('offline', detail)
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** this.attempt) * (0.5 + Math.random() / 2)
    this.attempt += 1
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = setTimeout(() => void this.connect(), delay)
  }

  private teardown(): void {
    this.epoch += 1
    clearTimeout(this.reconnectTimer)
    clearTimeout(this.pushTimer)
    clearInterval(this.pingTimer)
    this.abortInflight()
    const ws = this.ws
    this.ws = null
    if (ws && ws.readyState <= WebSocket.OPEN) ws.close(1000)
  }

  private abortInflight(): void {
    for (const controller of this.inflight.values()) controller.abort()
  }

  private send(ws: WebSocket, frame: unknown): void {
    if (ws === this.ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame))
  }

  private sendInventory(): void {
    const ws = this.ws
    if (!ws || this.status !== 'connected' || ws.readyState !== WebSocket.OPEN) return
    const frame = JSON.stringify({ type: 'inventory', servers: this.opts.host.inventory() })
    if (frame === this.lastInventory) return
    this.lastInventory = frame
    ws.send(frame)
  }

  private setStatus(status: BridgeStatus, detail: string | null): void {
    if (status === this.status && detail === this.detail) return
    this.status = status
    this.detail = detail
    this.opts.onChange()
  }

  // ── tool calls ──────────────────────────────────────────────────────────────
  private async handleCall(ws: WebSocket, frame: CallFrame): Promise<void> {
    const controller = new AbortController()
    this.inflight.set(frame.id, controller)
    let reply: Record<string, unknown>
    try {
      const found = this.opts.host.findTool(frame.server_id, frame.tool, frame.agent_id)
      if (!found) {
        throw new Error(
          `${frame.server_id}/${frame.tool} isn't available to the ${frame.agent_id} agent ` +
            'on this computer.',
        )
      }
      const args = frame.arguments ?? {}
      const key = `${frame.server_id}/${frame.tool}`
      if (needsConfirm(found.spec.builtin, found.tool) && !this.alwaysAllowed.has(key)) {
        const decision = await this.confirmInTurn(
          { agentId: frame.agent_id, serverName: found.spec.name, tool: frame.tool, args },
          controller.signal,
        )
        if (controller.signal.aborted) throw new Error('Cancelled: the agent stopped waiting.')
        if (decision === 'deny') throw new Error('The user declined this tool call.')
        if (decision === 'always') this.alwaysAllowed.add(key)
      }
      const content = await this.opts.host.call(frame.server_id, frame.tool, args)
      reply = { type: 'result', id: frame.id, ok: true, content }
    } catch (err) {
      reply = { type: 'result', id: frame.id, ok: false, error: message(err) }
    } finally {
      this.inflight.delete(frame.id)
    }
    this.send(ws, reply)
  }

  // One dialog at a time; a call cancelled while it waits never shows one.
  private confirmInTurn(request: ConfirmRequest, signal: AbortSignal): Promise<ConfirmDecision> {
    const turn = this.confirmQueue.then(
      (): ConfirmDecision | Promise<ConfirmDecision> =>
        signal.aborted ? 'deny' : this.opts.confirm(request, signal),
    )
    this.confirmQueue = turn.catch(() => undefined)
    return turn
  }
}
