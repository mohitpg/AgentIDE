import { useEffect, useState } from 'react'

import { getWorkspace, setWorkspace, type Workspace } from '../lib/api'

// The current-folder chip in the header. Shows the workspace folder name; click
// to switch it — via the native OS folder picker in Electron, or a typed-path
// prompt when that bridge isn't available (e.g. the in-app browser). The folder
// is host-wide (shared by every user's agents), so only admins can switch it.
function WorkspaceChip({ canChange }: { canChange: boolean }): JSX.Element {
  const [workspace, setWs] = useState<Workspace | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getWorkspace()
      .then(setWs)
      .catch(() => setWs(null))
  }, [])

  const pickFolder = async (): Promise<void> => {
    if (busy || !canChange) return
    setError(null)
    // Prefer the native dialog; fall back to typing a path in a plain browser.
    let chosen: string | null = null
    if (window.workspace?.selectFolder) {
      chosen = await window.workspace.selectFolder()
    } else {
      chosen = window.prompt('Workspace folder path', workspace?.path ?? '')
    }
    if (!chosen) return

    setBusy(true)
    try {
      setWs(await setWorkspace(chosen))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to switch folder')
    } finally {
      setBusy(false)
    }
  }

  const label = workspace?.name ?? '…'

  return (
    <button
      className="workspace-chip"
      onClick={pickFolder}
      disabled={busy || !canChange}
      title={
        error ??
        (canChange
          ? workspace?.path ?? 'Select workspace folder'
          : `${workspace?.path ?? ''} (only an admin can change the workspace)`)
      }
      aria-label={`Workspace: ${label}.${canChange ? ' Click to change.' : ''}`}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
      </svg>
      <span className="workspace-chip-name">{label}</span>
    </button>
  )
}

export default WorkspaceChip
