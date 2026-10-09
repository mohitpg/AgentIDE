"""
LLMAgentExecutor — adapts a `BaseAgent` to the A2A `AgentExecutor` interface.

A2A delivers a `Message`; the executor:
  1. resolves which skill to run (from message metadata `skill_id`, else default)
     and the caller's row scope (`user_id` / `project_id`, see `agent_common.scope`),
  2. streams the agent's text chunks back as `working` status updates (so the
     client renders tokens live),
  3. emits the full text as a final artifact and marks the task `completed`.

This one class serves every agent — agents only implement `BaseAgent.run`.
"""

from __future__ import annotations

import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState, TextPart
from a2a.utils import new_agent_text_message, new_task

from agent_common.base import BaseAgent
from agent_common.scope import parse_scope, reset_scope, set_scope

logger = logging.getLogger(__name__)


def _metadata(context: RequestContext) -> dict:
    meta = (context.message.metadata if context.message else None) or context.metadata or {}
    return meta if isinstance(meta, dict) else {}


def _resolve_skill_id(context: RequestContext, agent: BaseAgent) -> str:
    """Skill id is carried in message metadata; fall back to the agent's default."""
    skill_id = _metadata(context).get("skill_id")
    if skill_id and agent.get_skill(skill_id):
        return skill_id
    return agent.default_skill_id()


class LLMAgentExecutor(AgentExecutor):
    def __init__(self, agent: BaseAgent) -> None:
        self._agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        skill_id = _resolve_skill_id(context, self._agent)

        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        text = ""
        # The caller's user + project, visible to the agent via `current_scope()`.
        scope_token = set_scope(parse_scope(_metadata(context)))
        try:
            async for chunk in self._agent.run(skill_id, user_input, run_id=task.id):
                text += chunk
                await updater.update_status(
                    TaskState.working,
                    new_agent_text_message(chunk, task.context_id, task.id),
                )
        except Exception as exc:  # surface as a failed task rather than a 500
            logger.exception("agent %s skill %s failed", self._agent.id, skill_id)
            await updater.failed(
                new_agent_text_message(f"Error: {exc}", task.context_id, task.id)
            )
            return
        finally:
            reset_scope(scope_token)

        await updater.add_artifact(
            [Part(root=TextPart(text=text))],
            name=f"{self._agent.id}:{skill_id}",
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            updater = TaskUpdater(event_queue, task.id, task.context_id)
            await updater.update_status(TaskState.canceled, final=True)
