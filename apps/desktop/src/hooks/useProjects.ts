import { useCallback, useEffect, useState } from 'react'

import { createProject, deleteProject, listProjects } from '../lib/api'
import type { AuthUser } from '../lib/auth'
import type { Project } from '../types'

export interface UseProjects {
  projects: Project[]
  // The project all data requests run in; null until the list has loaded.
  activeId: string | null
  error: string | null
  reload: () => void
  select: (id: string) => void
  create: (name: string) => Promise<void>
  remove: (id: string) => Promise<void>
}

// Remember the last project per user (a per-device convenience; the server
// doesn't need it). Storage can be unavailable, so every access is guarded.
const storageKey = (userId: string): string => `agentide.activeProject.${userId}`

function remembered(userId: string): string | null {
  try {
    return localStorage.getItem(storageKey(userId))
  } catch {
    return null
  }
}

function remember(userId: string, id: string): void {
  try {
    localStorage.setItem(storageKey(userId), id)
  } catch {
    /* storage blocked — just don't persist */
  }
}

/**
 * The signed-in user's projects and which one is active. Every account has at
 * least one (the gateway creates "Default" on first sign-in).
 */
export function useProjects(user: AuthUser): UseProjects {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const select = useCallback(
    (id: string): void => {
      remember(user.id, id)
      setActiveId(id)
    },
    [user.id],
  )

  const reload = useCallback((): void => {
    setError(null)
    listProjects()
      .then((list) => {
        setProjects(list)
        setActiveId((cur) => {
          const want = cur ?? remembered(user.id)
          return list.some((p) => p.id === want) ? want : (list[0]?.id ?? null)
        })
      })
      .catch((e: Error) => setError(e.message))
  }, [user.id])

  useEffect(() => {
    reload()
  }, [reload])

  const create = useCallback(
    async (name: string): Promise<void> => {
      const project = await createProject(name)
      setProjects((prev) => [...prev, project])
      select(project.id)
    },
    [select],
  )

  const remove = useCallback(
    async (id: string): Promise<void> => {
      await deleteProject(id)
      const rest = projects.filter((p) => p.id !== id)
      setProjects(rest)
      if (id === activeId && rest[0]) select(rest[0].id)
    },
    [projects, activeId, select],
  )

  return { projects, activeId, error, reload, select, create, remove }
}
