// Types shared by the main process (electron/mcp/*) and the renderer, which
// reaches the local MCP servers only through the `window.mcp` IPC bridge.

export type McpTransport = 'stdio' | 'sse'

// Who may use a server and which of its tools; empty list = all.
export interface ServerExposure {
  enabled: boolean
  allowed_agents: string[]
  tool_allowlist: string[]
}

// A server the user added themselves (the builtins are defined in config.ts).
export interface CustomServer extends ServerExposure {
  id: string
  name: string
  description: string
  transport: McpTransport
  command: string
  args: string[]
  env: Record<string, string>
  cwd: string | null
  url: string
}

export type CustomServerInput = Pick<CustomServer, 'id' | 'transport'> &
  Partial<Omit<CustomServer, 'id' | 'transport'>>

export type ServerPatch = Partial<ServerExposure>

export type ServerStatus =
  | 'connected'
  | 'connecting'
  | 'error'
  | 'stopped'
  | 'disabled'
  | 'unavailable' // can't start yet: no workspace folder, or uvx missing

export interface McpToolView {
  name: string
  description: string
  confirm: boolean // calls ask the user first
}

export interface McpServerView extends ServerExposure {
  id: string
  name: string
  description: string
  builtin: boolean
  transport: McpTransport
  launch: string // how it runs, for display
  status: ServerStatus
  error: string | null
  tools: McpToolView[]
}

// The desktop's link to the backend, which relays agents' tool calls.
export type BridgeStatus =
  | 'signedOut'
  | 'connecting'
  | 'connected'
  | 'offline' // backend unreachable; retrying
  | 'replaced' // another AgentIDE window/device took over this account's tools

export interface WorkspaceInfo {
  path: string
  name: string
}

export interface McpState {
  workspace: WorkspaceInfo | null
  uvx: boolean
  bridge: { status: BridgeStatus; detail: string | null }
  servers: McpServerView[]
}
