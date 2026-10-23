import { useState } from 'react'

import type { UseMcp } from '../hooks/useMcp'
import {
  KNOWN_AGENTS,
  type BridgeStatus,
  type CustomServerInput,
  type McpServerView,
} from '../types'

interface Props {
  mcp: UseMcp
  onCollapse?: () => void
}

const BRIDGE_TEXT: Record<BridgeStatus, string> = {
  connected: 'Agents can use these tools',
  connecting: 'Connecting to the backend…',
  offline: 'Backend unreachable, retrying',
  replaced: 'In use from another AgentIDE window',
  signedOut: 'Sign in to lend these tools to agents',
}

const STATUS_TEXT: Record<McpServerView['status'], string> = {
  connected: 'running',
  connecting: 'starting…',
  error: 'error',
  stopped: 'stopped',
  disabled: 'off',
  unavailable: 'not ready',
}

function BridgeStatusRow({ mcp }: { mcp: UseMcp }): JSX.Element | null {
  const bridge = mcp.state?.bridge
  if (!bridge) return null
  return (
    <div className="mcp-status">
      <span className={`mcp-dot ${bridge.status}`} />
      <span className="mcp-status-text">
        {BRIDGE_TEXT[bridge.status]}
        {bridge.detail && <span className="mcp-status-detail">{bridge.detail}</span>}
      </span>
      {bridge.status === 'replaced' && (
        <button className="mcp-link-btn" onClick={() => void mcp.reconnect()}>
          Use here
        </button>
      )}
    </div>
  )
}

function ServerCard({ server, mcp }: { server: McpServerView; mcp: UseMcp }): JSX.Element {
  const toggleAgent = (agent: string): void => {
    const has = server.allowed_agents.includes(agent)
    const next = has
      ? server.allowed_agents.filter((a) => a !== agent)
      : [...server.allowed_agents, agent]
    void mcp.update(server.id, { allowed_agents: next })
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
          <span className="mcp-name">{server.name}</span>
        </label>
        <span className={`mcp-state ${server.status}`}>{STATUS_TEXT[server.status]}</span>
      </div>

      {server.description && <div className="mcp-desc">{server.description}</div>}
      <code className="mcp-cmd">{server.launch}</code>
      {server.error && <div className="mcp-card-error">{server.error}</div>}

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

      {server.tools.length > 0 && (
        <div className="mcp-tools">
          {server.tools.map((t) => (
            <span
              key={t.name}
              className={`mcp-tool ${t.confirm ? 'confirm' : ''}`}
              title={t.confirm ? `${t.description}\n\nAsks you before it runs.` : t.description}
            >
              {t.name}
            </span>
          ))}
        </div>
      )}

      <div className="mcp-card-foot">
        <button
          className="mcp-restart"
          onClick={() => void mcp.restart(server.id)}
          disabled={!server.enabled}
        >
          Restart
        </button>
        {!server.builtin && (
          <button className="mcp-remove" onClick={() => void mcp.remove(server.id)}>
            Remove
          </button>
        )}
      </div>
    </div>
  )
}

const EMPTY_FORM: CustomServerInput = {
  id: '',
  name: '',
  transport: 'stdio',
  command: '',
  url: '',
}

export default function McpPanel({ mcp, onCollapse }: Props): JSX.Element {
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState<CustomServerInput>(EMPTY_FORM)
  const [argsText, setArgsText] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const state = mcp.state

  const submit = async (): Promise<void> => {
    const error = await mcp.add({ ...form, args: argsText.split(/\s+/).filter(Boolean) })
    setFormError(error)
    if (error) return
    setForm(EMPTY_FORM)
    setArgsText('')
    setAdding(false)
  }

  return (
    <aside className="mcp">
      <div className="mcp-header">
        <span className="mcp-title">MCP Servers</span>
        <div className="mcp-header-actions">
          {mcp.available && (
            <button className="mcp-add-btn" onClick={() => setAdding((v) => !v)}>
              {adding ? 'Cancel' : '+ Add'}
            </button>
          )}
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
        {!mcp.available ? (
          <div className="mcp-empty">
            MCP servers run on your computer, inside the AgentIDE desktop app.
          </div>
        ) : (
          <>
            <BridgeStatusRow mcp={mcp} />
            {mcp.error && <div className="mcp-conn-error">{mcp.error}</div>}

            <div className="mcp-workspace">
              <span className="mcp-agents-label">Workspace</span>
              <div className="mcp-workspace-row">
                <code className="mcp-cmd" title={state?.workspace?.path}>
                  {state?.workspace?.path ?? 'No folder picked'}
                </code>
                <button className="mcp-link-btn" onClick={() => void mcp.pickWorkspace()}>
                  {state?.workspace ? 'Change' : 'Pick'}
                </button>
              </div>
            </div>

            {state && !state.uvx && (
              <div className="mcp-hint">
                Git and Web Fetch need uv (docs.astral.sh/uv). Install it, then restart
                them.
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
                  Add server
                </button>
              </div>
            )}

            {state?.servers.map((s) => <ServerCard key={s.id} server={s} mcp={mcp} />)}

            <div className="mcp-empty">
              Servers run on this computer; agents reach them through the backend.
              Amber tools ask you before they run.
            </div>
          </>
        )}
      </div>
    </aside>
  )
}
