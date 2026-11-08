"""Resolve resources only within the authenticated user's ownership boundary."""

from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import Project, Task, User


ResourceId = Annotated[int, Path(gt=0, le=2**31 - 1)]


def get_owned_project(
    project_id: ResourceId,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Project:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == current_user.id)
    )
    if project is None:
        # A foreign id must not reveal whether that project exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def get_owned_task(
    task_id: ResourceId,
    project: Annotated[Project, Depends(get_owned_project)],
    db: Annotated[Session, Depends(get_db)],
) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.project_id == project.id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task
