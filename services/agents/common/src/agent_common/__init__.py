"""agentide-agent-common: shared A2A server base (real a2a-sdk) for all agents."""

import os

os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

from agent_common.base import BaseAgent
from agent_common.client import A2AAgentClient
from agent_common.config import AgentSettings, get_agent_settings
from agent_common.executor import LLMAgentExecutor
from agent_common.llm import LLMConfig, build_chat_model, system_message
from agent_common.registration import RegistrationManager
from agent_common.server import create_agent_app
from agent_common.skills import SkillDefinition, load_skills

__all__ = [
    "A2AAgentClient",
    "AgentSettings",
    "BaseAgent",
    "LLMAgentExecutor",
    "LLMConfig",
    "RegistrationManager",
    "SkillDefinition",
    "build_chat_model",
    "create_agent_app",
    "get_agent_settings",
    "load_skills",
    "system_message",
]
