"""Project operations scoped to the authenticated owner."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.ownership import get_owned_project
from app.db.session import get_db
from app.models import Project, User
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate


router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Project:
    project = Project(name=payload.name, description=payload.description, owner_id=current_user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectRead)
def read_project(project: Annotated[Project, Depends(get_owned_project)]) -> Project:
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
) -> list[Project]:
    statement = (
        select(Project).where(Project.owner_id == current_user.id)
        .order_by(Project.id).offset(offset).limit(limit)
    )
    return list(db.scalars(statement))


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    payload: ProjectUpdate,
    project: Annotated[Project, Depends(get_owned_project)],
    db: Annotated[Session, Depends(get_db)],
) -> Project:
    # Only supplied, schema-allowlisted fields change; description=null is meaningful.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_project(
    project: Annotated[Project, Depends(get_owned_project)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
