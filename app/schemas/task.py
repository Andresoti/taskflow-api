"""Task request and public-response contracts."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models import TaskStatus


TaskTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00]*$")]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: TaskTitle
    description: str | None = Field(default=None, max_length=5000, pattern=r"^[^\x00]*$")
    status: TaskStatus = TaskStatus.pending


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: TaskTitle | None = None
    description: str | None = Field(default=None, max_length=5000, pattern=r"^[^\x00]*$")
    status: TaskStatus | None = None

    @field_validator("title", "status")
    @classmethod
    def reject_null_required_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("Task title and status must not be null")
        return value


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    status: TaskStatus
    project_id: int
    created_at: datetime
