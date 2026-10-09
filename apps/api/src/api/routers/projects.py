"""
/projects — the signed-in user's projects (the isolation boundary for user data).

  GET    /projects         list my projects (oldest first)
  POST   /projects         create one
  PATCH  /projects/{id}    rename
  DELETE /projects/{id}    delete it and everything in it (not the last one)

The desktop sends the active project as `X-Project-Id` on every data request;
see `api.deps.get_scope`.
"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_db
from api.deps import get_current_user, owned_project
from api.models.conversation import Conversation
from api.models.image import Image
from api.models.project import Project
from api.models.user import User
from api.routers.sessions import drop_chat_threads
from api.schemas.projects import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter()


async def _get_owned(db: AsyncSession, user: User, project_id: uuid.UUID) -> Project:
    project = await owned_project(db, user.id, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Project]:
    result = await db.execute(
        select(Project).where(Project.owner_id == user.id).order_by(Project.created_at)
    )
    return list(result.scalars().all())


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    project = Project(owner_id=user.id, name=payload.name.strip())
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.patch("/{project_id}", response_model=ProjectRead)
async def rename_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    project = await _get_owned(db, user, project_id)
    project.name = payload.name.strip()
    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    project = await _get_owned(db, user, project_id)
    count = await db.scalar(
        select(func.count()).select_from(Project).where(Project.owner_id == user.id)
    )
    if count <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Can't delete your only project")

    # Rows cascade with the project (FKs are ON DELETE CASCADE); the staged
    # image files on disk don't, so sweep them first.
    paths = await db.execute(
        select(Image.path).where(Image.project_id == project_id, Image.user_id == user.id)
    )
    for (path,) in paths.all():
        with contextlib.suppress(OSError):
            Path(path).unlink()
    session_ids = list(
        (
            await db.execute(
                select(Conversation.id).where(
                    Conversation.project_id == project_id, Conversation.user_id == user.id
                )
            )
        ).scalars()
    )

    await db.delete(project)
    await db.commit()
    await drop_chat_threads(request, session_ids)
