import { useEffect, useRef, useState } from 'react'

import type { ToolInvocation, TraceRun, TraceStep } from '../lib/trace'

interface Props {
  trace: TraceRun[]
  onCollapse?: () => void
}

// Stable accent per agent so the same specialist reads the same colour everywhere.
const AGENT_COLORS: Record<string, string> = {
  supervisor: '#a855f7',
  assistant: '#6366f1',
  code: '#22c55e',
  summarizer: '#0ea5e9',
  rag: '#f59e0b',
  vision: '#ec4899',
  web: '#14b8a6',
}

function agentColor(agent: string): string {
  return AGENT_COLORS[agent] ?? '#8b8b8b'
}

function preview(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function ToolRow({ tool }: { tool: ToolInvocation }): JSX.Element {
  const [open, setOpen] = useState(false)
  const hasDetail = tool.input !== undefined || tool.output !== undefined || tool.error
  return (
    <li className={`trace-tool ${tool.status}`}>
      <button
        className="trace-tool-head"
        onClick={() => hasDetail && setOpen((o) => !o)}
        disabled={!hasDetail}
      >
        <span className="trace-tool-icon" aria-hidden>
          {tool.status === 'running' ? '⏳' : tool.status === 'error' ? '✕' : '🔧'}
        </span>
        <span className="trace-tool-name">{tool.tool}</span>
        {hasDetail && <span className="trace-chevron">{open ? '▾' : '▸'}</span>}
      </button>
      {open && (
        <div className="trace-tool-detail">
          {tool.input !== undefined && (
            <div>
              <span className="trace-kv-key">input</span>
              <code>{preview(tool.input)}</code>
            </div>
          )}
          {tool.error ? (
            <div className="trace-error-text">
              <span className="trace-kv-key">error</span>
              <code>{tool.error}</code>
            </div>
          ) : (
            tool.output !== undefined && (
              <div>
                <span className="trace-kv-key">output</span>
                <code>{preview(tool.output)}</code>
              </div>
            )
          )}
        </div>
      )}
    </li>
  )
}

function StepRow({ step }: { step: TraceStep }): JSX.Element {
  const color = agentColor(step.agent)
  const routed = step.decisionAgent
    ? `→ ${step.decisionAgent}${step.decisionSkill ? ` · ${step.decisionSkill}` : ''}`
    : null
  return (
    <li className={`trace-step ${step.status}`}>
      <span className="trace-rail" style={{ background: color }} />
      <div className="trace-step-body">
        <div className="trace-step-head">
          <span className="trace-agent" style={{ color, borderColor: color }}>
            {step.agent}
          </span>
          <span className="trace-node">{step.node}</span>
          {step.skill && <span className="trace-skill">{step.skill}</span>}
          {step.status === 'running' && <span className="trace-dot" />}
        </div>
        {routed && <div className="trace-decision">{routed}</div>}
        {step.reason && <div className="trace-reason">{step.reason}</div>}
        {step.tools.length > 0 && (
          <ul className="trace-tools">
            {step.tools.map((t) => (
              <ToolRow key={t.callId} tool={t} />
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}

function RunCard({ run, index }: { run: TraceRun; index: number }): JSX.Element {
  const summary =
    run.durationMs != null
      ? `${run.totalTokens != null ? `${run.totalTokens} tok · ` : ''}${run.durationMs} ms`
      : null
  return (
    <div className={`trace-run ${run.status}`}>
      <div className="trace-run-head">
        <span className="trace-run-title">Run {index + 1}</span>
        <span className={`trace-run-status ${run.status}`}>{run.status}</span>
        {summary && <span className="trace-run-summary">{summary}</span>}
      </div>
      {run.steps.length === 0 ? (
        <div className="trace-run-pending">waiting for first step…</div>
      ) : (
        <ul className="trace-steps">
          {run.steps.map((s) => (
            <StepRow key={s.id} step={s} />
          ))}
        </ul>
      )}
      {run.error && <div className="trace-run-error">{run.error}</div>}
    </div>
  )
}

export default function TracePanel({ trace, onCollapse }: Props): JSX.Element {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Follow the live run as new steps stream in.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [trace])

  return (
    <aside className="trace">
      <div className="trace-header">
        <span className="trace-title">Agent trace</span>
        {onCollapse && (
          <button
            className="panel-collapse"
            title="Collapse agent trace"
            aria-label="Collapse agent trace"
            onClick={onCollapse}
          >
            ›
          </button>
        )}
      </div>
      <div className="trace-body" ref={scrollRef}>
        {trace.length === 0 ? (
          <div className="trace-empty">
            Send a message to see which agent runs, its steps, and tool calls.
          </div>
        ) : (
          trace.map((run, i) => <RunCard key={run.runId} run={run} index={i} />)
        )}
      </div>
    </aside>
  )
}
