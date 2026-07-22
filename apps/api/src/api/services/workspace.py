"""
Current workspace directory — the runtime-switchable `mcp_workspace_dir`.

Unlike the static `settings.mcp_workspace_dir` (loaded once from .env), this is a
process-local value the UI can change on the fly via ``PUT /workspace``. Anything
that roots the agents in a directory — the built-in filesystem/git MCP templates
(``GET /mcp/builtins``) and the code agent's filesystem sandbox — reads it here so
switching folders takes effect without an env change or restart.

In-memory only: seeded from ``settings.mcp_workspace_dir`` (or the process cwd) and
reset to that default whenever the gateway restarts.
"""

from __future__ import annotations

import os

from api.config import get_settings

# None until first read; then the current absolute workspace path.
_current: str | None = None


def _default() -> str:
    return get_settings().mcp_workspace_dir or os.getcwd()


def get_workspace() -> str:
    """The current workspace directory (seeded from settings on first access)."""
    global _current
    if _current is None:
        _current = os.path.abspath(_default())
    return _current


def set_workspace(path: str) -> str:
    """Validate ``path`` is an existing directory and make it the current workspace.

    Returns the normalized absolute path. Raises ``ValueError`` if the path is
    empty or does not point at a directory.
    """
    global _current
    candidate = os.path.abspath(os.path.expanduser(path.strip())) if path else ""
    if not candidate:
        raise ValueError("workspace path must not be empty")
    if not os.path.isdir(candidate):
        raise ValueError(f"not a directory: {candidate}")
    _current = candidate
    return _current
