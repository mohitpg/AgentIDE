// Builds an agent-trace view from the unified stream events.
//
// The supervisor and each agent emit `agent_step` (graph node enter/exit),
// `tool_call` / `tool_result`, and `done` / `error` events, all tagged with a
// `run_id`. `applyTraceEvent` folds that flat stream into a per-run tree the
// trace panel renders: which agent/node ran, the router's decision, and the
// tool calls nested under the step that made them.

import type { StreamEvent } from '../types'

export type StepStatus = 'running' | 'done'
export type ToolStatus = 'running' | 'done' | 'error'
export type RunStatus = 'running' | 'done' | 'error'

export interface ToolInvocation {
  callId: string
  tool: string
  agent?: string
  input?: unknown
  output?: unknown
  error?: string | null
  status: ToolStatus
}

export interface TraceStep {
  id: string
  agent: string
  node: string
  skill?: string | null
  // Supervisor decision (present on each `supervisor` step: the next agent, or
  // "respond"). The supervisor recurs, so a run holds one of these per loop pass.
  decisionAgent?: string
  decisionSkill?: string | null
  reason?: string
  status: StepStatus
  tools: ToolInvocation[]
}

export interface TraceRun {
  runId: string
  steps: TraceStep[]
  status: RunStatus
  totalTokens?: number
  durationMs?: number
  error?: string
}

interface Decision {
  next?: string // supervisor's chosen agent or "respond"
  agent?: string // legacy key, kept for backward compatibility
  skill?: string | null
  reason?: string
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

/** Index of the best step to hang a tool call on: the most recent running step. */
function targetStepIndex(steps: TraceStep[], agent?: string): number {
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].status === 'running' && (!agent || steps[i].agent === agent)) return i
  }
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].status === 'running') return i
  }
  return steps.length - 1 // -1 when there are no steps yet
}

function withToolUpdate(
  steps: TraceStep[],
  callId: string,
  patch: Partial<ToolInvocation>,
): TraceStep[] {
  return steps.map((step) => {
    if (!step.tools.some((t) => t.callId === callId)) return step
    return {
      ...step,
      tools: step.tools.map((t) => (t.callId === callId ? { ...t, ...patch } : t)),
    }
  })
}

function closeRunningSteps(steps: TraceStep[]): TraceStep[] {
  return steps.map((s) => (s.status === 'running' ? { ...s, status: 'done' } : s))
}

/** Fold one stream event into the list of runs. Token events are ignored here. */
export function applyTraceEvent(runs: TraceRun[], event: StreamEvent): TraceRun[] {
  if (event.type === 'token') return runs

  const { run_id: runId, data } = event
  const next = runs.slice()
  let idx = next.findIndex((r) => r.runId === runId)
  if (idx === -1) {
    next.push({ runId, steps: [], status: 'running' })
    idx = next.length - 1
  }
  const run: TraceRun = { ...next[idx], steps: next[idx].steps.slice() }

  switch (event.type) {
    case 'agent_step': {
      const phase = str(data.phase)
      const agent = str(data.agent, 'agent')
      const node = str(data.node)
      if (phase === 'enter') {
        // Key by occurrence (steps.length), so each loop pass of a recurring node
        // (e.g. the supervisor) is its own step rather than colliding by name.
        run.steps.push({
          id: `${runId}:${agent}:${node}:${run.steps.length}`,
          agent,
          node,
          skill: typeof data.skill === 'string' ? data.skill : undefined,
          status: 'running',
          tools: [],
        })
      } else if (phase === 'exit') {
        // Close the most recent still-running step matching this node — enter/exit
        // are balanced per pass, so this resolves each recurrence in order.
        for (let i = run.steps.length - 1; i >= 0; i--) {
          const s = run.steps[i]
          if (s.agent === agent && s.node === node && s.status === 'running') {
            const decision = data.decision as Decision | undefined
            run.steps[i] = {
              ...s,
              status: 'done',
              skill: s.skill ?? (typeof data.skill === 'string' ? data.skill : s.skill),
              decisionAgent: decision?.next ?? decision?.agent ?? s.decisionAgent,
              decisionSkill: decision?.skill ?? s.decisionSkill,
              reason: decision?.reason ?? s.reason,
            }
            break
          }
        }
      }
      break
    }

    case 'tool_call': {
      const callId = str(data.call_id) || event.id
      const agent = typeof data.agent === 'string' ? data.agent : undefined
      const tool: ToolInvocation = {
        callId,
        tool: str(data.tool, 'tool'),
        agent,
        input: data.input,
        status: 'running',
      }
      const target = targetStepIndex(run.steps, agent)
      if (target === -1) {
        run.steps.push({
          id: `${runId}:tool:${run.steps.length}`,
          agent: agent ?? 'agent',
          node: 'tool',
          status: 'running',
          tools: [tool],
        })
      } else {
        run.steps[target] = {
          ...run.steps[target],
          tools: [...run.steps[target].tools, tool],
        }
      }
      break
    }

    case 'tool_result': {
      const callId = str(data.call_id)
      const error = typeof data.error === 'string' ? data.error : null
      run.steps = withToolUpdate(run.steps, callId, {
        output: data.output,
        error,
        status: error ? 'error' : 'done',
      })
      break
    }

    case 'error': {
      // A recoverable error (e.g. one specialist down) doesn't fail the run — the
      // supervisor continues and the terminal `done` closes it. Leave status be.
      if (data.recoverable === true) break
      run.status = 'error'
      run.error = str(data.message, 'Something went wrong')
      run.steps = closeRunningSteps(run.steps)
      break
    }

    case 'done': {
      run.status = 'done'
      if (typeof data.total_tokens === 'number') run.totalTokens = data.total_tokens
      if (typeof data.duration_ms === 'number') run.durationMs = data.duration_ms
      run.steps = closeRunningSteps(run.steps)
      break
    }

    default:
      break
  }

  next[idx] = run
  return next
}
