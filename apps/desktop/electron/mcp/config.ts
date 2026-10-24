// Local MCP server definitions, kept in <userData>/mcp-servers.json.
//
// MCP servers run on this machine only. The backend sees server ids, exposure
// rules and tool schemas (see bridge.ts), never how a server is launched.
//
// Three builtins ship with the app; they can be disabled but not removed:
//   filesystem  the bundled @modelcontextprotocol/server-filesystem, run by
//               Electron's own Node (ELECTRON_RUN_AS_NODE), rooted at the workspace
//   git         `uvx mcp-server-git` on the workspace
//   fetch       `uvx mcp-server-fetch`
// filesystem and git need a workspace folder; git and fetch need uv's `uvx`.

import { execFile } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import type {
  CustomServer,
  McpTransport,
  ServerExposure,
  WorkspaceInfo,
} from './types'

export const BUILTIN_IDS = ['filesystem', 'git', 'fetch'] as const
type BuiltinId = (typeof BUILTIN_IDS)[number]

const NO_WORKSPACE = 'Pick a workspace folder first.'
const UVX_HINT = 'Needs uv: install it from https://docs.astral.sh/uv/, then restart this server.'
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/

// Everything needed to start one server, plus how to show it.
export interface LaunchSpec extends ServerExposure {
  id: string
  name: string
  description: string
  builtin: boolean
  transport: McpTransport
  command: string
  args: string[]
  env: Record<string, string>
  cwd: string | null
  url: string
  launch: string
  unavailable: string | null // why it can't start, if it can't
}

interface StoredConfig {
  version: 1
  workspace: string | null
  builtins: Record<BuiltinId, ServerExposure>
  servers: CustomServer[]
}

const defaultExposure = (): ServerExposure => ({
  enabled: true,
  allowed_agents: [],
  tool_allowlist: [],
})

// GUI apps (macOS ones especially) start with a minimal PATH, so also look in
// the usual install locations for uv.
export function childPath(): string {
  const home = os.homedir()
  const extra = [path.join(home, '.local', 'bin'), path.join(home, '.cargo', 'bin')]
  if (process.platform !== 'win32') extra.push('/opt/homebrew/bin', '/usr/local/bin')
  const current = (process.env.PATH ?? '').split(path.delimiter).filter(Boolean)
  return [...current, ...extra.filter((dir) => !current.includes(dir))].join(path.delimiter)
}

export function hasUvx(): Promise<boolean> {
  return new Promise((resolve) => {
    execFile(
      'uvx',
      ['--version'],
      { env: { ...process.env, PATH: childPath() }, timeout: 10_000, windowsHide: true },
      (err) => resolve(!err),
    )
  })
}

export function workspaceInfo(dir: string): WorkspaceInfo {
  // basename, but the whole path for a drive root like "C:\".
  return { path: dir, name: path.basename(dir) || dir }
}

export class McpConfigStore {
  private data: StoredConfig

  constructor(private readonly file: string) {
    this.data = load(file)
  }

  get workspace(): WorkspaceInfo | null {
    return this.data.workspace ? workspaceInfo(this.data.workspace) : null
  }

  setWorkspace(dir: string): WorkspaceInfo {
    const resolved = path.resolve(dir)
    if (!fs.statSync(resolved, { throwIfNoEntry: false })?.isDirectory()) {
      throw new Error(`Not a folder: ${resolved}`)
    }
    this.data.workspace = resolved
    this.save()
    return workspaceInfo(resolved)
  }

  specs(filesystemScript: string, uvx: boolean): LaunchSpec[] {
    const ws = this.data.workspace
    const { filesystem, git, fetch } = this.data.builtins
    const builtins: LaunchSpec[] = [
      {
        ...filesystem,
        id: 'filesystem',
        name: 'Filesystem',
        description: `Read and write files in ${ws ?? 'the workspace folder'}`,
        builtin: true,
        transport: 'stdio',
        command: process.execPath,
        args: ws ? [filesystemScript, ws] : [],
        env: { ELECTRON_RUN_AS_NODE: '1' },
        cwd: null,
        url: '',
        launch: 'bundled @modelcontextprotocol/server-filesystem',
        unavailable: ws ? null : NO_WORKSPACE,
      },
      {
        ...git,
        id: 'git',
        name: 'Git',
        description: `Git operations on ${ws ?? 'the workspace folder'}`,
        builtin: true,
        transport: 'stdio',
        command: 'uvx',
        args: ws ? ['mcp-server-git', '--repository', ws] : [],
        env: {},
        cwd: null,
        url: '',
        launch: 'uvx mcp-server-git',
        unavailable: !ws ? NO_WORKSPACE : uvx ? null : UVX_HINT,
      },
      {
        ...fetch,
        id: 'fetch',
        name: 'Web Fetch',
        description: 'Fetch a URL and extract its content as markdown',
        builtin: true,
        transport: 'stdio',
        command: 'uvx',
        args: ['mcp-server-fetch'],
        env: {},
        cwd: null,
        url: '',
        launch: 'uvx mcp-server-fetch',
        unavailable: uvx ? null : UVX_HINT,
      },
    ]
    const custom = this.data.servers.map(
      (s): LaunchSpec => ({
        ...s,
        builtin: false,
        launch: s.transport === 'stdio' ? [s.command, ...s.args].join(' ') : s.url,
        unavailable: null,
      }),
    )
    return [...builtins, ...custom]
  }

  add(input: unknown): void {
    const server = parseCustom(input)
    if (this.data.servers.some((s) => s.id === server.id)) {
      throw new Error(`A server called "${server.id}" already exists`)
    }
    this.data.servers.push(server)
    this.save()
  }

  // Exposure only (enable, agents, tool allow-list); to change how a custom
  // server launches, remove it and add it again.
  update(id: string, patch: unknown): void {
    const changes = isObject(patch) ? patch : {}
    if (isBuiltin(id)) {
      this.data.builtins[id] = parseExposure(changes, this.data.builtins[id])
    } else {
      const index = this.data.servers.findIndex((s) => s.id === id)
      if (index < 0) throw new Error(`No server called "${id}"`)
      const server = this.data.servers[index]
      this.data.servers[index] = { ...server, ...parseExposure(changes, server) }
    }
    this.save()
  }

  remove(id: string): void {
    if (isBuiltin(id)) throw new Error('Built-in servers can be disabled but not removed')
    this.data.servers = this.data.servers.filter((s) => s.id !== id)
    this.save()
  }

  private save(): void {
    fs.mkdirSync(path.dirname(this.file), { recursive: true })
    const tmp = `${this.file}.tmp`
    fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2))
    fs.renameSync(tmp, this.file)
  }
}

// ── parsing (the file and IPC input are both untrusted) ──────────────────────
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isBuiltin(id: string): id is BuiltinId {
  return (BUILTIN_IDS as readonly string[]).includes(id)
}

const str = (value: unknown): string => (typeof value === 'string' ? value : '')

const strList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []

function strMap(value: unknown): Record<string, string> {
  if (!isObject(value)) return {}
  return Object.fromEntries(
    Object.entries(value).filter((e): e is [string, string] => typeof e[1] === 'string'),
  )
}

function parseExposure(input: Record<string, unknown>, base: ServerExposure): ServerExposure {
  return {
    enabled: typeof input.enabled === 'boolean' ? input.enabled : base.enabled,
    allowed_agents: Array.isArray(input.allowed_agents)
      ? strList(input.allowed_agents)
      : base.allowed_agents,
    tool_allowlist: Array.isArray(input.tool_allowlist)
      ? strList(input.tool_allowlist)
      : base.tool_allowlist,
  }
}

function parseCustom(input: unknown): CustomServer {
  if (!isObject(input)) throw new Error('Invalid server')
  const id = str(input.id).trim()
  if (!ID_RE.test(id) || id.includes('__')) {
    throw new Error('The id must be 1–64 letters, digits, "-" or "_", without "__"')
  }
  if (isBuiltin(id)) throw new Error(`"${id}" is a built-in server`)
  const transport = input.transport
  if (transport !== 'stdio' && transport !== 'sse') {
    throw new Error('The transport must be stdio or sse')
  }
  const server: CustomServer = {
    id,
    transport,
    name: str(input.name).trim() || id,
    description: str(input.description),
    command: str(input.command).trim(),
    args: strList(input.args),
    env: strMap(input.env),
    cwd: str(input.cwd) || null,
    url: str(input.url).trim(),
    ...parseExposure(input, defaultExposure()),
  }
  if (server.transport === 'stdio' && !server.command) {
    throw new Error('A stdio server needs a command')
  }
  if (server.transport === 'sse' && !/^https?:\/\//.test(server.url)) {
    throw new Error('An SSE server needs an http(s) URL')
  }
  return server
}

function load(file: string): StoredConfig {
  const config: StoredConfig = {
    version: 1,
    workspace: null,
    builtins: { filesystem: defaultExposure(), git: defaultExposure(), fetch: defaultExposure() },
    servers: [],
  }
  let raw: unknown
  try {
    raw = JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch {
    return config // first run, or unreadable: start from the defaults
  }
  if (!isObject(raw)) return config

  config.workspace = str(raw.workspace) || null
  const builtins = isObject(raw.builtins) ? raw.builtins : {}
  for (const id of BUILTIN_IDS) {
    const stored = builtins[id]
    if (isObject(stored)) config.builtins[id] = parseExposure(stored, defaultExposure())
  }
  for (const entry of Array.isArray(raw.servers) ? raw.servers : []) {
    try {
      config.servers.push(parseCustom(entry))
    } catch (err) {
      console.warn('[mcp] skipping invalid server in config:', err)
    }
  }
  return config
}
