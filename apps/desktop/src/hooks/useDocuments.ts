import { useCallback, useEffect, useState } from 'react'

import { deleteDocument, listDocuments } from '../lib/api'
import type { RagDocument } from '../types'

export interface UseDocuments {
  documents: RagDocument[]
  refresh: () => void
  remove: (id: string) => Promise<void>
}

/**
 * Owns the indexed-document list backing the Sources panel. Loaded from
 * GET /rag/documents; `refresh()` is called after an upload finishes indexing.
 */
export function useDocuments(): UseDocuments {
  const [documents, setDocuments] = useState<RagDocument[]>([])

  const refresh = useCallback((): void => {
    listDocuments()
      .then(setDocuments)
      .catch(() => {
        /* backend down — keep what we have */
      })
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const remove = useCallback(async (id: string): Promise<void> => {
    await deleteDocument(id)
    setDocuments((prev) => prev.filter((d) => d.id !== id))
  }, [])

  return { documents, refresh, remove }
}
