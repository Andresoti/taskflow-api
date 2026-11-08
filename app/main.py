"""TaskFlow's HTTP application and validated authentication startup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.auth_config import AuthSettings
from app.routers import auth, projects, tasks, users


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.auth_settings = AuthSettings()
    yield


app = FastAPI(title="TaskFlow API", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, error: RequestValidationError) -> JSONResponse:
    # FastAPI's default errors include submitted values, including passwords.
    details = [
        {key: item[key] for key in ("type", "loc", "msg")}
        for item in error.errors()
    ]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": details})


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(tasks.router)
