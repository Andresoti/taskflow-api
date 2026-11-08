"""Task operations nested under an authorized project."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ownership import get_owned_project, get_owned_task
from app.db.session import get_db
from app.models import Project, Task
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate


router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["Tasks"])


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    project: Annotated[Project, Depends(get_owned_project)],
    db: Annotated[Session, Depends(get_db)],
) -> Task:
    task = Task(
        title=payload.title, description=payload.description,
        status=payload.status, project_id=project.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/{task_id}", response_model=TaskRead)
def read_task(task: Annotated[Task, Depends(get_owned_task)]) -> Task:
    return task


@router.get("", response_model=list[TaskRead])
def list_tasks(
    project: Annotated[Project, Depends(get_owned_project)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
) -> list[Task]:
    statement = (
        select(Task).where(Task.project_id == project.id)
        .order_by(Task.id).offset(offset).limit(limit)
    )
    return list(db.scalars(statement))


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(
    payload: TaskUpdate,
    task: Annotated[Task, Depends(get_owned_task)],
    db: Annotated[Session, Depends(get_db)],
) -> Task:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_task(
    task: Annotated[Task, Depends(get_owned_task)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
