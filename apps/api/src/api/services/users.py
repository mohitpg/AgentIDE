"""
Account provisioning on sign-in.

`upsert_google_user` maps a verified Google identity onto a `users` row: it
matches on Google's stable `sub` first, then on the (Google-verified) email so a
pre-existing account gets linked rather than duplicated. Every user always has at
least one project, so a fresh account gets a "Default" project.

Admin bootstrap: when `AUTH_ADMIN_EMAILS` is set, admin status is synced to it on
every sign-in. With it unset, the very first account becomes the admin. The first
account also adopts rows created
before auth existed (their owner columns are NULL), so a single-user install keeps
its chat history, tasks, uploads, and indexed documents.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Uuid, bindparam, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.conversation import Conversation
from api.models.image import Image
from api.models.project import Project
from api.models.task import Task
from api.models.user import User
from api.services.google_oauth import GoogleIdentity

DEFAULT_PROJECT_NAME = "Default"


class AccountDisabledError(Exception):
    pass


async def ensure_default_project(db: AsyncSession, user: User) -> Project:
    """Return the user's oldest project, creating "Default" if they have none."""
    project = (
        await db.execute(
            select(Project)
            .where(Project.owner_id == user.id)
            .order_by(Project.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if project is None:
        project = Project(owner_id=user.id, name=DEFAULT_PROJECT_NAME)
        db.add(project)
        await db.flush()
    return project


async def adopt_legacy_rows(db: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Assign every pre-auth (ownerless) row to ``user_id`` / ``project_id``."""
    owner = {"user_id": user_id, "project_id": project_id}
    for model in (Conversation, Task, Image):
        await db.execute(update(model).where(model.user_id.is_(None)).values(**owner))
    # rag_documents isn't an ORM model (packages/rag owns it via raw SQL).
    await db.execute(
        text(
            "UPDATE rag_documents SET user_id = :user_id, project_id = :project_id "
            "WHERE user_id IS NULL"
        ).bindparams(bindparam("user_id", type_=Uuid), bindparam("project_id", type_=Uuid)),
        owner,
    )


async def upsert_google_user(
    db: AsyncSession, identity: GoogleIdentity, settings: Settings
) -> User:
    user = (
        await db.execute(
            select(User)
            .where(or_(User.google_sub == identity.sub, User.email == identity.email))
            # Prefer the sub match if both exist on different rows.
            .order_by((User.google_sub == identity.sub).desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    is_first = False
    if user is None:
        is_first = (await db.scalar(select(func.count()).select_from(User))) == 0
        user = User(email=identity.email, google_sub=identity.sub)
        db.add(user)
    elif not user.is_active:
        raise AccountDisabledError(identity.email)

    user.google_sub = identity.sub
    user.email = identity.email
    user.display_name = identity.name or user.display_name
    user.avatar_url = identity.picture or user.avatar_url

    # A configured admin list is authoritative (also demotes); otherwise only
    # the first account is promoted and nobody is ever demoted.
    admins = settings.admin_emails
    if admins:
        user.is_superuser = identity.email in admins
    elif is_first:
        user.is_superuser = True
    await db.flush()

    project = await ensure_default_project(db, user)
    if is_first:
        await adopt_legacy_rows(db, user.id, project.id)
    return user
