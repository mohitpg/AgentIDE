// Entry point for the bundled filesystem MCP server. vite.config.ts builds it
// into dist-electron/mcp-filesystem.js with its dependencies inlined, and
// host.ts runs it with Electron's own Node (ELECTRON_RUN_AS_NODE=1), so users
// don't need Node installed. Its arguments are the folders it may touch.
import '@modelcontextprotocol/server-filesystem/dist/index.js'
