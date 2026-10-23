// Runs the local MCP servers (see config.ts) as an MCP client, via the SDK.
//
// Each enabled server is started as a child process (stdio) or reached over SSE,
// initialized, and asked for its tools. A server that fails is reported in its
// status and simply contributes no tools; the others are unaffected.

import { app } from 'electron'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { SSEClientTransport } from '@modelcontextprotocol/sdk/client/sse.js'
import {
  StdioClientTransport,
  getDefaultEnvironment,
} from '@modelcontextprotocol/sdk/client/stdio.js'
import type { Transport } from '@modelcontextprotocol/sdk/shared/transport.js'

import { childPath, type LaunchSpec } from './config'
import type { McpServerView, ServerStatus } from './types'

export interface HostTool {
  name: string
  description: string
  inputSchema: Record<string, unknown>
  readOnly: boolean // the server's readOnlyHint annotation
}

// What the backend learns about a server: never its command.
export interface InventoryServer {
  id: string
  name: string
  allowed_agents: string[]
  tool_allowlist: string[]
  tools: { name: string; description: string; input_schema: Record<string, unknown> }[]
}

interface Entry {
  spec: LaunchSpec
  key: string // launch parameters; a change means a restart
  client: Client | null
  status: ServerStatus
  error: string | null
  tools: HostTool[]
  generation: number // bumped on every (re)start/stop; stale starts back off
}

// Builtin tools that only read. Any other tool asks the user before it runs,
// unless its server marks it read-only.
const BUILTIN_READ_ONLY =
  /^(read_|list_|get_|search_|directory_tree$|fetch$|git_(status|diff\w*|log|show|branch)$)/

export function needsConfirm(builtin: boolean, tool: HostTool): boolean {
  if (tool.readOnly) return false
  return !(builtin && BUILTIN_READ_ONLY.test(tool.name))
}

export class McpHost {
  private readonly entries = new Map<string, Entry>()

  constructor(private readonly onChange: () => void) {}

  // Bring the running servers in line with ``specs``: start new or changed ones,
  // stop removed or disabled ones. ``restartId`` restarts that server regardless.
  async sync(specs: LaunchSpec[], restartId?: string): Promise<void> {
    const wanted = new Set(specs.map((s) => s.id))
    for (const [id, entry] of this.entries) {
      if (!wanted.has(id)) {
        this.entries.delete(id)
        void this.disconnect(entry)
      }
    }

    const starts: Promise<void>[] = []
    for (const spec of specs) {
      const key = launchKey(spec)
      let entry = this.entries.get(spec.id)
      if (!entry) {
        entry = { spec, key, client: null, status: 'stopped', error: null, tools: [], generation: 0 }
        this.entries.set(spec.id, entry)
      }
      const relaunch = entry.key !== key || spec.id === restartId
      entry.spec = spec
      entry.key = key

      if (!spec.enabled || spec.unavailable) {
        await this.disconnect(entry)
        entry.status = spec.enabled ? 'unavailable' : 'disabled'
        entry.error = spec.enabled ? spec.unavailable : null
        continue
      }
      if (relaunch || ['stopped', 'disabled', 'unavailable'].includes(entry.status)) {
        starts.push(this.connect(entry))
      }
    }
    this.onChange()
    await Promise.all(starts)
  }

  views(): McpServerView[] {
    return [...this.entries.values()].map(({ spec, status, error, tools }) => ({
      id: spec.id,
      name: spec.name,
      description: spec.description,
      builtin: spec.builtin,
      transport: spec.transport,
      launch: spec.launch,
      enabled: spec.enabled,
      allowed_agents: spec.allowed_agents,
      tool_allowlist: spec.tool_allowlist,
      status,
      error,
      tools: tools.map((t) => ({
        name: t.name,
        description: t.description,
        confirm: needsConfirm(spec.builtin, t),
      })),
    }))
  }

  inventory(): InventoryServer[] {
    return [...this.entries.values()]
      .filter((e) => e.client !== null && e.status === 'connected')
      .map(({ spec, tools }) => ({
        id: spec.id,
        name: spec.name,
        allowed_agents: spec.allowed_agents,
        tool_allowlist: spec.tool_allowlist,
        tools: tools.map((t) => ({
          name: t.name,
          description: t.description,
          input_schema: t.inputSchema,
        })),
      }))
  }

  // The tool, if it's running and exposed to ``agentId`` on this machine. The
  // backend filters too; this re-checks against the local config.
  findTool(
    serverId: string,
    toolName: string,
    agentId: string,
  ): { spec: LaunchSpec; tool: HostTool } | null {
    const entry = this.entries.get(serverId)
    if (!entry?.client || entry.status !== 'connected') return null
    const { spec } = entry
    if (spec.allowed_agents.length && !spec.allowed_agents.includes(agentId)) return null
    if (spec.tool_allowlist.length && !spec.tool_allowlist.includes(toolName)) return null
    const tool = entry.tools.find((t) => t.name === toolName)
    return tool ? { spec, tool } : null
  }

  async call(serverId: string, toolName: string, args: Record<string, unknown>): Promise<string> {
    const client = this.entries.get(serverId)?.client
    if (!client) throw new Error(`The ${serverId} server isn't running`)
    const result: unknown = await client.callTool({ name: toolName, arguments: args })
    const text = resultText(result)
    if ((result as { isError?: boolean }).isError) {
      throw new Error(text || `${toolName} failed`)
    }
    return text
  }

  async closeAll(): Promise<void> {
    await Promise.all([...this.entries.values()].map((e) => this.disconnect(e)))
  }

  // ── internals ───────────────────────────────────────────────────────────────
  private async connect(entry: Entry): Promise<void> {
    await this.disconnect(entry)
    const generation = entry.generation
    const { spec } = entry
    entry.status = 'connecting'
    entry.error = null
    this.onChange()

    let stderr = ''
    const client = new Client({ name: 'agentide-desktop', version: app.getVersion() })
    try {
      let transport: Transport
      if (spec.transport === 'sse') {
        transport = new SSEClientTransport(new URL(spec.url))
      } else {
        const stdio = new StdioClientTransport({
          command: spec.command,
          args: spec.args,
          env: { ...getDefaultEnvironment(), PATH: childPath(), ...spec.env },
          cwd: spec.cwd ?? undefined,
          stderr: 'pipe',
        })
        // Keep the tail of the server's stderr: it usually says why it died.
        stdio.stderr?.on('data', (chunk: Buffer) => {
          stderr = (stderr + chunk.toString()).slice(-2000)
        })
        transport = stdio
      }
      await client.connect(transport)
      const tools = await listTools(client)
      if (generation !== entry.generation) {
        await client.close().catch(() => undefined) // stopped or restarted meanwhile
        return
      }
      client.onclose = () => {
        if (entry.client !== client) return
        entry.client = null
        entry.tools = []
        entry.status = 'error'
        entry.error = describe(new Error('The server stopped'), spec, stderr)
        this.onChange()
      }
      entry.client = client
      entry.tools = tools
      entry.status = 'connected'
    } catch (err) {
      await client.close().catch(() => undefined)
      if (generation !== entry.generation) return
      entry.status = 'error'
      entry.error = describe(err, spec, stderr)
    }
    this.onChange()
  }

  private async disconnect(entry: Entry): Promise<void> {
    entry.generation += 1
    const client = entry.client
    entry.client = null
    entry.tools = []
    if (entry.status === 'connected' || entry.status === 'connecting') entry.status = 'stopped'
    if (client) await client.close().catch(() => undefined)
  }
}

function launchKey(spec: LaunchSpec): string {
  const { transport, command, args, env, cwd, url } = spec
  return JSON.stringify({ transport, command, args, env, cwd, url })
}

async function listTools(client: Client): Promise<HostTool[]> {
  const tools: HostTool[] = []
  let cursor: string | undefined
  do {
    const page = await client.listTools(cursor ? { cursor } : undefined)
    for (const tool of page.tools) {
      tools.push({
        name: tool.name,
        description: tool.description ?? '',
        inputSchema: tool.inputSchema as Record<string, unknown>,
        readOnly: tool.annotations?.readOnlyHint === true,
      })
    }
    cursor = page.nextCursor
  } while (cursor)
  return tools
}

// Flatten a tool result into the text the model sees. Images and other binary
// blocks are summarized rather than inlined.
function resultText(result: unknown): string {
  const r = result as { content?: unknown; structuredContent?: unknown }
  const blocks = Array.isArray(r.content) ? r.content : []
  const parts = blocks.map((block) => {
    const b = block as {
      type?: string
      text?: string
      mimeType?: string
      uri?: string
      resource?: { text?: string; uri?: string }
    }
    if (b.type === 'text' && typeof b.text === 'string') return b.text
    if (b.type === 'resource' && b.resource) return b.resource.text ?? b.resource.uri ?? ''
    if (b.type === 'resource_link' && b.uri) return b.uri
    return `[${b.type ?? 'unknown'} content${b.mimeType ? ` (${b.mimeType})` : ''} omitted]`
  })
  if (!parts.length && r.structuredContent !== undefined) {
    return JSON.stringify(r.structuredContent)
  }
  return parts.join('\n')
}

function describe(err: unknown, spec: LaunchSpec, stderr: string): string {
  let message = err instanceof Error ? err.message : String(err)
  if (/ENOENT/.test(message)) message = `Command not found: ${spec.command}`
  const tail = stderr.trim().split(/\r?\n/).slice(-4).join('\n')
  return tail ? `${message}\n${tail}` : message
}
