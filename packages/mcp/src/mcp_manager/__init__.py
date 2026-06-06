"""
agentide-mcp: MCP manager.

Implemented in Phase 6 – MCP extensibility.
Handles:
  - MCP server process lifecycle (stdio / SSE transport)
  - Capability discovery (tools, resources, prompts)
  - Per-agent tool allow-lists
  - Settings-driven registration (no code changes needed for new servers)
"""
