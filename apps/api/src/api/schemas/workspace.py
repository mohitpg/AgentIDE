"""Pydantic DTOs for the /workspace API."""

from __future__ import annotations

from pydantic import BaseModel


class WorkspaceRead(BaseModel):
    """The current workspace: absolute path + its display (basename) name."""

    path: str
    name: str


class WorkspaceUpdate(BaseModel):
    """Switch the workspace to ``path`` (must be an existing directory)."""

    path: str
