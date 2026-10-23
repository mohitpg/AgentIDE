import { ipcRenderer, contextBridge, type IpcRendererEvent } from 'electron'

import type { CustomServerInput, McpState, ServerPatch, WorkspaceInfo } from './mcp/types'

// --------- Expose some API to the Renderer process ---------
contextBridge.exposeInMainWorld('ipcRenderer', {
  on(...args: Parameters<typeof ipcRenderer.on>) {
    const [channel, listener] = args
    return ipcRenderer.on(channel, (event, ...args) => listener(event, ...args))
  },
  off(...args: Parameters<typeof ipcRenderer.off>) {
    const [channel, ...omit] = args
    return ipcRenderer.off(channel, ...omit)
  },
  send(...args: Parameters<typeof ipcRenderer.send>) {
    const [channel, ...omit] = args
    return ipcRenderer.send(channel, ...omit)
  },
  invoke(...args: Parameters<typeof ipcRenderer.invoke>) {
    const [channel, ...omit] = args
    return ipcRenderer.invoke(channel, ...omit)
  },

  // You can expose other APTs you need here.
  // ...
})

// --------- Expose backend API base URL to Renderer ---------
// The main process decides it (electron/auth.ts) and passes it as a launch
// argument, so the renderer and main always talk to the same backend.
const API_URL_ARG = '--agentide-api-url='
contextBridge.exposeInMainWorld('api', {
  baseUrl:
    process.argv.find((arg) => arg.startsWith(API_URL_ARG))?.slice(API_URL_ARG.length) ??
    'http://localhost:8000',
})

// --------- Google sign-in (flow + refresh token live in the main process) ---------
contextBridge.exposeInMainWorld('auth', {
  login: () => ipcRenderer.invoke('auth:login'),
  refresh: () => ipcRenderer.invoke('auth:refresh'),
  logout: () => ipcRenderer.invoke('auth:logout'),
})

// --------- Local MCP servers (run by the main process, see electron/mcp) ---------
contextBridge.exposeInMainWorld('mcp', {
  get: (): Promise<McpState> => ipcRenderer.invoke('mcp:get'),
  add: (server: CustomServerInput): Promise<McpState> => ipcRenderer.invoke('mcp:add', server),
  update: (id: string, patch: ServerPatch): Promise<McpState> =>
    ipcRenderer.invoke('mcp:update', id, patch),
  remove: (id: string): Promise<McpState> => ipcRenderer.invoke('mcp:remove', id),
  restart: (id: string): Promise<McpState> => ipcRenderer.invoke('mcp:restart', id),
  reconnect: (): Promise<McpState> => ipcRenderer.invoke('mcp:reconnect'),
  // Pushed whenever a server or the backend link changes; returns an unsubscribe.
  onChange: (listener: (state: McpState) => void): (() => void) => {
    const handler = (_event: IpcRendererEvent, state: McpState) => listener(state)
    ipcRenderer.on('mcp:changed', handler)
    return () => {
      ipcRenderer.off('mcp:changed', handler)
    }
  },
})

// --------- Workspace folder (roots the filesystem + git MCP servers) ---------
contextBridge.exposeInMainWorld('workspace', {
  get: (): Promise<WorkspaceInfo | null> => ipcRenderer.invoke('workspace:get'),
  // Native folder picker; resolves to the new workspace, or null if cancelled.
  pick: (): Promise<WorkspaceInfo | null> => ipcRenderer.invoke('workspace:pick'),
})
