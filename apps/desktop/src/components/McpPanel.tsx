import { useState } from 'react'

import type { UseMcp } from '../hooks/useMcp'
import type { MCPServerCreate } from '../lib/api'
import { KNOWN_AGENTS, type MCPDiscoverResponse, type MCPServer } from '../types'

interface Props {
  mcp: UseMcp
  onCollapse?: () => void
}

function ServerCard({
  server,
  mcp,
}: {
  server: MCPServer
  mcp: UseMcp
}): JSX.Element {
  const [discovery, setDiscovery] = useState<MCPDiscoverResponse | null>(null)
  const [busy, setBusy] = useState(false)

  const toggleAgent = (agent: string): void => {
    const has = server.allowed_agents.includes(agent)
    const next = has
      ? server.allowed_agents.filter((a) => a !== agent)
      : [...server.allowed_agents, agent]
    void mcp.update(server.id, { allowed_agents: next })
  }

  const runDiscover = async (): Promise<void> => {
    setBusy(true)
    try {
      setDiscovery(await mcp.discover(server.id))
    } catch (e) {
      setDiscovery({
        server_id: server.id,
        connected: false,
        tools: [],
        error: (e as Error).message,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`mcp-card ${server.enabled ? '' : 'disabled'}`}>
      <div className="mcp-card-head">
        <label className="mcp-enable" title="Enable / disable this server">
          <input
            type="checkbox"
            checked={server.enabled}
            onChange={(e) => void mcp.update(server.id, { enabled: e.target.checked })}
          />
          <span className="mcp-name">{server.name || server.id}</span>
        </label>
        <span className="mcp-transport">{server.transport}</span>
      </div>

      <code className="mcp-cmd">
        {server.transport === 'stdio'
          ? [server.command, ...server.args].join(' ')
          : server.url}
      </code>

      <div className="mcp-agents">
        <span className="mcp-agents-label">
          Agents{server.allowed_agents.length === 0 ? ' (all)' : ''}
        </span>
        <div className="mcp-agent-chips">
          {KNOWN_AGENTS.map((agent) => (
            <button
              key={agent}
              className={`mcp-chip ${server.allowed_agents.includes(agent) ? 'on' : ''}`}
              onClick={() => toggleAgent(agent)}
            >
              {agent}
            </button>
          ))}
        </div>
      </div>

      <div className="mcp-card-foot">
        <button className="mcp-discover" onClick={runDiscover} disabled={busy}>
          {busy ? 'Connecting…' : 'Discover tools'}
        </button>
        <button className="mcp-remove" onClick={() => void mcp.remove(server.id)}>
          Remove
        </button>
      </div>

      {discovery && (
        <div className={`mcp-tools ${discovery.connected ? '' : 'error'}`}>
          {discovery.connected ? (
            discovery.tools.length ? (
              discovery.tools.map((t) => (
                <span key={t.name} className="mcp-tool" title={t.description}>
                  {t.name}
                </span>
              ))
            ) : (
              <span className="mcp-tool-empty">No tools advertised</span>
            )
          ) : (
            <span className="mcp-tool-error">{discovery.error}</span>
          )}
        </div>
      )}
    </div>
  )
}

const EMPTY_FORM: MCPServerCreate = {
  id: '',
  name: '',
  transport: 'stdio',
  command: '',
  args: [],
}

export default function McpPanel({ mcp, onCollapse }: Props): JSX.Element {
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState<MCPServerCreate>(EMPTY_FORM)
  const [argsText, setArgsText] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  const registeredIds = new Set(mcp.servers.map((s) => s.id))
  const unregisteredBuiltins = mcp.builtins.filter((b) => !registeredIds.has(b.id))

  const submit = async (): Promise<void> => {
    setFormError(null)
    try {
      await mcp.add({
        ...form,
        name: form.name || form.id,
        args: argsText.split(/\s+/).filter(Boolean),
      })
      setForm(EMPTY_FORM)
      setArgsText('')
      setAdding(false)
    } catch (e) {
      setFormError((e as Error).message)
    }
  }

  return (
    <aside className="mcp">
      <div className="mcp-header">
        <span className="mcp-title">MCP Servers</span>
        <div className="mcp-header-actions">
          <button className="mcp-add-btn" onClick={() => setAdding((v) => !v)}>
            {adding ? 'Cancel' : '+ Add'}
          </button>
          {onCollapse && (
            <button
              className="panel-collapse"
              title="Collapse MCP panel"
              aria-label="Collapse MCP panel"
              onClick={onCollapse}
            >
              ›
            </button>
          )}
        </div>
      </div>

      <div className="mcp-body">
        {mcp.error && <div className="mcp-conn-error">Backend unreachable: {mcp.error}</div>}

        {unregisteredBuiltins.length > 0 && (
          <div className="mcp-builtins">
            <span className="mcp-builtins-label">Quick add</span>
            <div className="mcp-builtin-chips">
              {unregisteredBuiltins.map((b) => (
                <button
                  key={b.id}
                  className="mcp-builtin-chip"
                  title={b.description ?? b.id}
                  onClick={() => void mcp.add(b)}
                >
                  + {b.name || b.id}
                </button>
              ))}
            </div>
          </div>
        )}

        {adding && (
          <div className="mcp-form">
            <input
              placeholder="id (e.g. github)"
              value={form.id}
              onChange={(e) => setForm({ ...form, id: e.target.value })}
            />
            <input
              placeholder="name"
              value={form.name ?? ''}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
            <select
              value={form.transport}
              onChange={(e) =>
                setForm({ ...form, transport: e.target.value as 'stdio' | 'sse' })
              }
            >
              <option value="stdio">stdio</option>
              <option value="sse">sse</option>
            </select>
            {form.transport === 'stdio' ? (
              <>
                <input
                  placeholder="command (e.g. npx)"
                  value={form.command ?? ''}
                  onChange={(e) => setForm({ ...form, command: e.target.value })}
                />
                <input
                  placeholder="args (space-separated)"
                  value={argsText}
                  onChange={(e) => setArgsText(e.target.value)}
                />
              </>
            ) : (
              <input
                placeholder="url (http://…/sse)"
                value={form.url ?? ''}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
              />
            )}
            {formError && <div className="mcp-form-error">{formError}</div>}
            <button className="mcp-form-submit" onClick={submit} disabled={!form.id}>
              Register
            </button>
          </div>
        )}

        {mcp.servers.length === 0 && !adding ? (
          <div className="mcp-empty">
            No MCP servers registered. Quick-add filesystem or git above, then assign
            them to an agent — its tools become available live.
          </div>
        ) : (
          mcp.servers.map((s) => <ServerCard key={s.id} server={s} mcp={mcp} />)
        )}
      </div>
    </aside>
  )
}
