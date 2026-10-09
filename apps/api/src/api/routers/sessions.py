"""
/sessions – conversation CRUD + history, scoped to the caller's active project.

"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.session import get_db
from api.deps import Scope, get_scope
from api.models.conversation import Conversation
from api.models.image import Image
from api.schemas.chat import SessionDetail, SessionRead

router = APIRouter()


def _in_scope(scope: Scope):  # type: ignore[no-untyped-def]
    return (Conversation.user_id == scope.user_id, Conversation.project_id == scope.project_id)


@router.get("", response_model=list[SessionRead])
async def list_sessions(
    scope: Scope = Depends(get_scope), db: AsyncSession = Depends(get_db)
) -> list[Conversation]:
    result = await db.execute(
        select(Conversation).where(*_in_scope(scope)).order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: uuid.UUID,
    scope: Scope = Depends(get_scope),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == session_id, *_in_scope(scope))
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return conversation


async def drop_chat_threads(request: Request, session_ids: list[uuid.UUID]) -> None:
    """Delete the orchestrator's checkpointed state for deleted conversations."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None and session_ids:
        await orchestrator.delete_threads([str(s) for s in session_ids])


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    request: Request,
    scope: Scope = Depends(get_scope),
    db: AsyncSession = Depends(get_db),
) -> None:
    conversation = (
        await db.execute(
            select(Conversation).where(Conversation.id == session_id, *_in_scope(scope))
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    # Sweep this session's staged image files before the rows cascade away (the
    # `images.session_id` FK is ON DELETE CASCADE, so the rows go with the row).
    paths = await db.execute(
        select(Image.path).where(Image.session_id == session_id)
    )
    for (path,) in paths.all():
        with contextlib.suppress(OSError):
            Path(path).unlink()

    await db.delete(conversation)

    await db.commit()
    await drop_chat_threads(request, [session_id])
