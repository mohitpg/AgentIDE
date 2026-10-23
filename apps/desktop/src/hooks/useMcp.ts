import { useCallback, useEffect, useState } from 'react'

import { cleanError } from '../lib/auth'
import type { CustomServerInput, McpState, ServerPatch } from '../types'

type McpApi = NonNullable<Window['mcp']>

export interface UseMcp {
  // null until the first state arrives, and always outside the desktop app.
  state: McpState | null
  available: boolean
  // The last failed action, for the panel to show.
  error: string | null
  // Resolves to an error message, or null once the server is added.
  add: (server: CustomServerInput) => Promise<string | null>
  update: (id: string, patch: ServerPatch) => Promise<void>
  remove: (id: string) => Promise<void>
  restart: (id: string) => Promise<void>
  reconnect: () => Promise<void>
  pickWorkspace: () => Promise<void>
}

/**
 * The local MCP servers, run by the desktop's main process and lent to the
 * cloud agents over the bridge. The main process pushes a fresh state whenever
 * a server or the backend link changes; actions also return one.
 */
export function useMcp(): UseMcp {
  const [state, setState] = useState<McpState | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const api = window.mcp
    if (!api) return
    let live = true
    void api.get().then((s) => {
      if (live) setState(s)
    })
    const unsubscribe = api.onChange(setState)
    return () => {
      live = false
      unsubscribe()
    }
  }, [])

  // Run an IPC action; returns its error message (also kept in `error`), or null.
  const run = useCallback(async (action: (api: McpApi) => Promise<McpState>) => {
    if (!window.mcp) return 'Local tools need the AgentIDE desktop app.'
    try {
      setState(await action(window.mcp))
      setError(null)
      return null
    } catch (err) {
      const message = cleanError(err)
      setError(message)
      return message
    }
  }, [])

  const add = useCallback((server: CustomServerInput) => run((api) => api.add(server)), [run])
  const update = useCallback(
    async (id: string, patch: ServerPatch): Promise<void> => {
      await run((api) => api.update(id, patch))
    },
    [run],
  )
  const remove = useCallback(
    async (id: string): Promise<void> => {
      await run((api) => api.remove(id))
    },
    [run],
  )
  const restart = useCallback(
    async (id: string): Promise<void> => {
      await run((api) => api.restart(id))
    },
    [run],
  )
  const reconnect = useCallback(async (): Promise<void> => {
    await run((api) => api.reconnect())
  }, [run])

  const pickWorkspace = useCallback(async (): Promise<void> => {
    const api = window.mcp
    if (!api || !window.workspace) return
    try {
      if (await window.workspace.pick()) setState(await api.get())
      setError(null)
    } catch (err) {
      setError(cleanError(err))
    }
  }, [])

  return {
    state,
    available: Boolean(window.mcp),
    error,
    add,
    update,
    remove,
    restart,
    reconnect,
    pickWorkspace,
  }
}
