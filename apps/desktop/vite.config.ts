import { defineConfig } from 'vite'
import path from 'node:path'
import electron from 'vite-plugin-electron/simple'
import react from '@vitejs/plugin-react'

// Backend URL baked into the main process at build time. CI sets
// AGENTIDE_API_URL to the dev or prod API when it builds installers; local
// builds default to the API on this machine.
const apiUrl = process.env.AGENTIDE_API_URL ?? 'http://localhost:8000'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    electron({
      main: {
        // Shortcut of `build.lib.entry`. The filesystem MCP server is a second
        // entry so it ships as its own script with its dependencies inlined
        // (Electron runs it as Node; see electron/mcp/filesystem-server.ts).
        entry: {
          main: 'electron/main.ts',
          'mcp-filesystem': 'electron/mcp/filesystem-server.ts',
        },
        vite: {
          define: { __AGENTIDE_API_URL__: JSON.stringify(apiUrl) },
          // Main-process code runs on Electron's Node (v22+), not a browser; the
          // filesystem server needs top-level await, which the default target lacks.
          build: { target: 'node22' },
        },
      },
      preload: {
        // Shortcut of `build.rollupOptions.input`.
        // Preload scripts may contain Web assets, so use the `build.rollupOptions.input` instead `build.lib.entry`.
        input: path.join(__dirname, 'electron/preload.ts'),
      },
      // Ployfill the Electron and Node.js API for Renderer process.
      // If you want use Node.js in Renderer process, the `nodeIntegration` needs to be enabled in the Main process.
      // See 👉 https://github.com/electron-vite/vite-plugin-electron-renderer
      renderer: process.env.NODE_ENV === 'test'
        // https://github.com/electron-vite/vite-plugin-electron-renderer/issues/78#issuecomment-2053600808
        ? undefined
        : {},
    }),
  ],
})
