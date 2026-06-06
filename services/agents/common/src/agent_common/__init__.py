"""agentide-agent-common: shared base for all A2A agent services."""

from agent_common.server import create_agent_app
from agent_common.base import BaseAgent

__all__ = ["create_agent_app", "BaseAgent"]
