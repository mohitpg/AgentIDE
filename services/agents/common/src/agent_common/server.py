"""
A2A FastAPI server factory.

Call `create_agent_app(agent)` to get a ready-to-run FastAPI app that:
  - Serves the Agent Card at /.well-known/agent.json
  - Accepts skill invocations at /skills/{skill_id}
  - Streams events via SSE at /skills/{skill_id}/stream
  - Health-checks at /health
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_common.base import BaseAgent


class SkillRequest(BaseModel):
    input: dict[str, Any]
    run_id: str | None = None


def create_agent_app(agent: BaseAgent) -> FastAPI:
    card = agent.card
    app = FastAPI(title=card.name, version=card.version)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "agent": card.id}

    @app.get("/.well-known/agent.json")
    async def agent_card() -> dict[str, Any]:
        return card.model_dump(mode="json")

    @app.post("/skills/{skill_id}")
    async def invoke_skill(skill_id: str, req: SkillRequest) -> dict[str, Any]:
        if agent.get_skill(skill_id) is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
        run_id = req.run_id or str(uuid.uuid4())
        return await agent.execute_skill(skill_id, req.input, run_id)

    @app.post("/skills/{skill_id}/stream")
    async def stream_skill(skill_id: str, req: SkillRequest) -> StreamingResponse:
        if agent.get_skill(skill_id) is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
        run_id = req.run_id or str(uuid.uuid4())

        import json

        async def event_generator():
            async for event in agent.stream_skill(skill_id, req.input, run_id):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return app
