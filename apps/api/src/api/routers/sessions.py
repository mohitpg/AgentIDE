"""
/sessions – conversation CRUD + history.

Phase 1 has no auth yet, so sessions are not user-scoped. Phase 7 adds JWT and
row-level scoping by `user_id` (the column already exists).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.session import get_db
from api.models.conversation import Conversation
from api.schemas.chat import SessionDetail, SessionRead

router = APIRouter()


@router.get("", response_model=list[SessionRead])
async def list_sessions(db: AsyncSession = Depends(get_db)) -> list[Conversation]:
    result = await db.execute(
        select(Conversation).order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == session_id)
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return conversation


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    conversation = await db.get(Conversation, session_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    await db.delete(conversation)
    # Commit here (like the other write routes) so the row is gone before the
    # 204 returns; otherwise get_db's deferred commit races the client's refresh,
    # making deletes lag by a click.
    await db.commit()
