/// <reference types="vite-plugin-electron/electron-env" />

declare namespace NodeJS {
  interface ProcessEnv {
    /**
     * The built directory structure
     *
     * ```tree
     * ├─┬─┬ dist
     * │ │ └── index.html
     * │ │
     * │ ├─┬ dist-electron
     * │ │ ├── main.js
     * │ │ └── preload.js
     * │
     * ```
     */
    APP_ROOT: string
    /** /dist/ or /public/ */
    VITE_PUBLIC: string
  }
}

// The backend URL, defined at build time for the main process (vite.config.ts).
declare const __AGENTIDE_API_URL__: string

// Used in Renderer process, expose in `preload.ts`
interface Window {
  ipcRenderer: import('electron').IpcRenderer
  api: { baseUrl: string }
  // Local MCP servers + the workspace folder, run by the main process
  // (electron/mcp). Absent in a plain browser, where there are no local tools.
  mcp?: {
    get: () => Promise<import('./mcp/types').McpState>
    add: (server: import('./mcp/types').CustomServerInput) => Promise<import('./mcp/types').McpState>
    update: (id: string, patch: import('./mcp/types').ServerPatch) => Promise<import('./mcp/types').McpState>
    remove: (id: string) => Promise<import('./mcp/types').McpState>
    restart: (id: string) => Promise<import('./mcp/types').McpState>
    reconnect: () => Promise<import('./mcp/types').McpState>
    onChange: (listener: (state: import('./mcp/types').McpState) => void) => () => void
  }
  workspace?: {
    get: () => Promise<import('./mcp/types').WorkspaceInfo | null>
    pick: () => Promise<import('./mcp/types').WorkspaceInfo | null>
  }
  // Google sign-in bridge (preload → main). Absent in a plain browser, where
  // sign-in isn't possible. `refresh` resolves null once the session is over.
  auth?: {
    login: () => Promise<import('./auth').AuthSession>
    refresh: () => Promise<import('./auth').AuthSession | null>
    logout: () => Promise<void>
  }
}
