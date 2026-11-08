"""Dependencies shared by authentication and protected routes."""

import re
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.auth_config import AuthSettings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def get_auth_settings(request: Request) -> AuthSettings:
    """Reuse the authentication configuration validated at API startup."""
    return request.app.state.auth_settings


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
    auth_settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> User:
    failure = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = decode_access_token(token, secret_key=auth_settings.jwt_secret_key.get_secret_value())
    except InvalidTokenError:
        raise failure from None

    subject = claims["sub"]
    # User ids are positive PostgreSQL INTEGER values, not arbitrary JWT strings.
    if not isinstance(subject, str) or re.fullmatch(r"[1-9][0-9]{0,9}", subject) is None:
        raise failure
    user_id = int(subject)
    if user_id > 2**31 - 1:
        raise failure
    user = db.get(User, user_id)
    if user is None:
        raise failure
    return user
