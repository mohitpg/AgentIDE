import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  net,
  protocol,
  type MessageBoxOptions,
  type OpenDialogOptions,
} from 'electron'
//import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

import * as auth from './auth'
import { McpBridge, type ConfirmDecision, type ConfirmRequest } from './mcp/bridge'
import { hasUvx, McpConfigStore } from './mcp/config'
import { McpHost } from './mcp/host'
import type { McpState } from './mcp/types'

//const require = createRequire(import.meta.url)
const __dirname = path.dirname(fileURLToPath(import.meta.url))

// The built directory structure
//
// ├─┬─┬ dist
// │ │ └── index.html
// │ │
// │ ├─┬ dist-electron
// │ │ ├── main.js
// │ │ └── preload.mjs
// │
process.env.APP_ROOT = path.join(__dirname, '..')

// 🚧 Use ['ENV_NAME'] avoid vite:define plugin - Vite@2.x
export const VITE_DEV_SERVER_URL = process.env['VITE_DEV_SERVER_URL']
export const MAIN_DIST = path.join(process.env.APP_ROOT, 'dist-electron')
export const RENDERER_DIST = path.join(process.env.APP_ROOT, 'dist')

process.env.VITE_PUBLIC = VITE_DEV_SERVER_URL ? path.join(process.env.APP_ROOT, 'public') : RENDERER_DIST

// The built renderer is served from app://agentide instead of file://, so it
// has a real origin: the backend's CORS allows app://agentide, and absolute
// asset paths (/assets/…) resolve. Must be registered before the app is ready.
const APP_SCHEME = 'app'
const APP_HOST = 'agentide'
protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } },
])

// Map app://agentide/<path> onto a file in the renderer build (dist/).
function serveRenderer(request: Request): Promise<Response> | Response {
  const { host, pathname } = new URL(request.url)
  if (host !== APP_HOST) return new Response('Not found', { status: 404 })
  const decoded = decodeURIComponent(pathname)
  const file = path.join(RENDERER_DIST, decoded === '/' ? 'index.html' : decoded)
  // Never serve anything outside dist/ (e.g. app://agentide/../../secret).
  const inside = path.relative(RENDERER_DIST, file)
  if (inside.startsWith('..') || path.isAbsolute(inside)) {
    return new Response('Forbidden', { status: 403 })
  }
  return net.fetch(pathToFileURL(file).toString())
}

let win: BrowserWindow | null

function createWindow() {
  win = new BrowserWindow({
    icon: path.join(process.env.VITE_PUBLIC, 'electron-vite.svg'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.mjs'),
      // The preload exposes this as window.api.baseUrl.
      additionalArguments: [`--agentide-api-url=${auth.API_BASE}`],
    },
  })

  // Test active push message to Renderer-process.
  win.webContents.on('did-finish-load', () => {
    win?.webContents.send('main-process-message', (new Date).toLocaleString())
  })

  if (VITE_DEV_SERVER_URL) {
    win.loadURL(VITE_DEV_SERVER_URL)
  } else {
    win.loadURL(`${APP_SCHEME}://${APP_HOST}/`)
  }
}

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
    win = null
  }
})

app.on('activate', () => {
  // On OS X it's common to re-create a window in the app when the
  // dock icon is clicked and there are no other windows open.
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})

// ── Local MCP servers, lent to the user's cloud agents over the bridge ─────────
// (see ./mcp). The workspace folder roots the filesystem and git servers.
let mcpConfig: McpConfigStore | null = null
let uvx = false
const mcpHost = new McpHost(onMcpChange)
const mcpBridge = new McpBridge({
  apiBase: auth.API_BASE,
  getToken: auth.accessToken,
  host: mcpHost,
  confirm: confirmToolCall,
  onChange: onMcpChange,
})

function mcpStore(): McpConfigStore {
  mcpConfig ??= new McpConfigStore(path.join(app.getPath('userData'), 'mcp-servers.json'))
  return mcpConfig
}

function mcpState(): McpState {
  return {
    workspace: mcpStore().workspace,
    uvx,
    bridge: mcpBridge.state,
    servers: mcpHost.views(),
  }
}

function onMcpChange(): void {
  if (win && !win.isDestroyed()) win.webContents.send('mcp:changed', mcpState())
  mcpBridge.pushInventory()
}

// Bundled filesystem MCP server, built by vite next to main.js. In a packaged
// app it's unpacked from the asar (electron-builder `asarUnpack`) so
// Electron-as-Node can run it as a plain file.
function filesystemServerScript(): string {
  return path
    .join(MAIN_DIST, 'mcp-filesystem.js')
    .replace(`app.asar${path.sep}`, `app.asar.unpacked${path.sep}`)
}

// Start/stop servers to match the config. Callers don't wait for servers to
// come up: their progress reaches the renderer as `mcp:changed` events.
async function applyMcpConfig(restartId?: string): Promise<void> {
  uvx = await hasUvx()
  await mcpHost.sync(mcpStore().specs(filesystemServerScript(), uvx), restartId)
}

// Ask before a tool that can change things runs on this computer.
async function confirmToolCall(
  request: ConfirmRequest,
  signal: AbortSignal,
): Promise<ConfirmDecision> {
  const args = JSON.stringify(request.args, null, 2)
  const options: MessageBoxOptions = {
    type: 'question',
    buttons: ['Allow', 'Deny'],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
    title: 'Allow tool call?',
    message: `The ${request.agentId} agent wants to run ${request.tool} (${request.serverName}) on this computer.`,
    detail: args.length > 1500 ? `${args.slice(0, 1500)}\n…` : args,
    checkboxLabel: `Always allow ${request.tool} until I sign out`,
    signal, // the backend gave up waiting: close the dialog (counts as Deny)
  }
  const parent = win && !win.isDestroyed() ? win : null
  if (parent && !parent.isFocused()) parent.flashFrame(true)
  const { response, checkboxChecked } = parent
    ? await dialog.showMessageBox(parent, options)
    : await dialog.showMessageBox(options)
  parent?.flashFrame(false)
  if (response !== 0) return 'deny'
  return checkboxChecked ? 'always' : 'allow'
}

const runMcp = (action: () => void): McpState => {
  action()
  void applyMcpConfig()
  return mcpState()
}

ipcMain.handle('mcp:get', () => mcpState())
ipcMain.handle('mcp:add', (_event, server: unknown) => runMcp(() => mcpStore().add(server)))
ipcMain.handle('mcp:update', (_event, id: unknown, patch: unknown) =>
  runMcp(() => mcpStore().update(String(id), patch)),
)
ipcMain.handle('mcp:remove', (_event, id: unknown) => runMcp(() => mcpStore().remove(String(id))))
ipcMain.handle('mcp:restart', (_event, id: unknown) => {
  void applyMcpConfig(String(id))
  return mcpState()
})
ipcMain.handle('mcp:reconnect', () => {
  mcpBridge.reconnect()
  return mcpState()
})

// Native "Select Folder" dialog for the workspace switcher. Resolves to the new
// workspace, or null if the user cancelled.
ipcMain.handle('workspace:get', () => mcpStore().workspace)
ipcMain.handle('workspace:pick', async () => {
  const options: OpenDialogOptions = {
    title: 'Select workspace folder',
    properties: ['openDirectory', 'createDirectory'],
  }
  const result = win && !win.isDestroyed()
    ? await dialog.showOpenDialog(win, options)
    : await dialog.showOpenDialog(options)
  if (result.canceled || result.filePaths.length === 0) return null
  const workspace = mcpStore().setWorkspace(result.filePaths[0])
  void applyMcpConfig()
  return workspace
})

// Google sign-in + session tokens (see ./auth.ts). The refresh token never
// crosses into the renderer; these return only the access token + user. A live
// session also connects the MCP bridge; signing out disconnects it.
ipcMain.handle('auth:login', async () => {
  const session = await auth.login()
  mcpBridge.start()
  return session
})
ipcMain.handle('auth:refresh', async () => {
  const session = await auth.refresh()
  if (session) mcpBridge.start()
  else mcpBridge.stop()
  return session
})
ipcMain.handle('auth:logout', async () => {
  mcpBridge.stop()
  await auth.logout()
})

// Stop the MCP server processes before quitting so none outlive the app.
let quitting = false
app.on('before-quit', (event) => {
  if (quitting) return
  quitting = true
  event.preventDefault()
  mcpBridge.stop()
  const deadline = new Promise((resolve) => setTimeout(resolve, 3000))
  void Promise.race([mcpHost.closeAll(), deadline]).finally(() => app.quit())
})

app.whenReady().then(() => {
  protocol.handle(APP_SCHEME, serveRenderer)
  void applyMcpConfig()
  createWindow()
})
