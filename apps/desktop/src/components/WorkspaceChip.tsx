import type { UseMcp } from '../hooks/useMcp'

// The current-folder chip in the header: the workspace the filesystem and git
// MCP servers are rooted in. Those servers run on this computer, so the folder
// is this user's own choice; click to pick one with the native folder dialog.
function WorkspaceChip({ mcp }: { mcp: UseMcp }): JSX.Element {
  const workspace = mcp.state?.workspace ?? null
  const label = workspace?.name ?? 'Pick a folder'

  return (
    <button
      className="workspace-chip"
      onClick={() => void mcp.pickWorkspace()}
      disabled={!mcp.available}
      title={
        mcp.available
          ? workspace?.path ?? 'Select workspace folder'
          : 'Workspace folders need the AgentIDE desktop app'
      }
      aria-label={`Workspace: ${label}.${mcp.available ? ' Click to change.' : ''}`}
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
