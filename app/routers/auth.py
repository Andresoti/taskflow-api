"""Registration and login endpoints."""

from datetime import timedelta
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_config import AuthSettings
from app.core.dependencies import get_auth_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import User
from app.schemas.auth import TokenResponse
from app.schemas.user import EmailInput, UserCreate, UserRead


router = APIRouter(prefix="/auth", tags=["Authentication"])
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreate, db: Annotated[Session, Depends(get_db)]) -> User:
    user = User(
        email=str(payload.email),
        hashed_password=hash_password(payload.password.get_secret_value()),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        # The unique constraint also covers concurrent registrations.
        if getattr(error.orig, "pgcode", None) == "23505":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email is already registered",
            ) from None
        raise
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(
    response: Response,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
    auth_settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> TokenResponse:
    failure = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if len(form.password) > 128:
        raise failure
    try:
        email = EmailInput(email=form.username).email
    except ValidationError:
        raise failure from None
    user = db.scalar(select(User).where(User.email == email))
    # Verify a hash on both paths; never reveal which credential was wrong.
    password_hash = user.hashed_password if user is not None else _DUMMY_PASSWORD_HASH
    valid_password = verify_password(form.password, password_hash)
    if user is None or not valid_password:
        raise failure

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(
        access_token=create_access_token(
            str(user.id),
            secret_key=auth_settings.jwt_secret_key.get_secret_value(),
            expires_delta=timedelta(minutes=auth_settings.access_token_expire_minutes),
        )
    )
