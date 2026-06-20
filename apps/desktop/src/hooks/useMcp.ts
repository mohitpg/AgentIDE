import { useCallback, useEffect, useState } from 'react'

import {
  createMcpServer,
  deleteMcpServer,
  discoverMcpServer,
  listMcpBuiltins,
  listMcpServers,
  updateMcpServer,
  type MCPServerCreate,
  type MCPServerUpdate,
} from '../lib/api'
import type { MCPDiscoverResponse, MCPServer } from '../types'

export interface UseMcp {
  servers: MCPServer[]
  builtins: MCPServerCreate[]
  error: string | null
  refresh: () => void
  add: (body: MCPServerCreate) => Promise<void>
  update: (id: string, body: MCPServerUpdate) => Promise<void>
  remove: (id: string) => Promise<void>
  discover: (id: string) => Promise<MCPDiscoverResponse>
}

/**
 * Owns the registered MCP server list + the built-in templates. CRUD calls
 * re-sync from the backend so the list always reflects server state; `discover`
 * connects a server live and returns its tools (not cached — it's an action).
 */
export function useMcp(): UseMcp {
  const [servers, setServers] = useState<MCPServer[]>([])
  const [builtins, setBuiltins] = useState<MCPServerCreate[]>([])
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback((): void => {
    listMcpServers()
      .then((s) => {
        setServers(s)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(() => {
    refresh()
    listMcpBuiltins()
      .then(setBuiltins)
      .catch(() => {
        /* templates are a convenience; ignore if backend is down */
      })
  }, [refresh])

  const add = useCallback(
    async (body: MCPServerCreate): Promise<void> => {
      await createMcpServer(body)
      refresh()
    },
    [refresh],
  )

  const update = useCallback(
    async (id: string, body: MCPServerUpdate): Promise<void> => {
      // Optimistic: patch the row in place, then re-sync to confirm.
      setServers((prev) => prev.map((s) => (s.id === id ? { ...s, ...body } : s)))
      await updateMcpServer(id, body)
      refresh()
    },
    [refresh],
  )

  const remove = useCallback(
    async (id: string): Promise<void> => {
      await deleteMcpServer(id)
      setServers((prev) => prev.filter((s) => s.id !== id))
    },
    [],
  )

  const discover = useCallback((id: string) => discoverMcpServer(id), [])

  return { servers, builtins, error, refresh, add, update, remove, discover }
}
