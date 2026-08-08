from __future__ import annotations

import secrets
from typing import Annotated, NoReturn

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from karakeep_opds.config import Settings, get_settings

security = HTTPBasic(auto_error=False)


def require_basic_auth(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if credentials is None:
        raise_auth_error()

    username_ok = secrets.compare_digest(credentials.username, settings.opds_username)
    password_ok = secrets.compare_digest(credentials.password, settings.opds_password)
    if not (username_ok and password_ok):
        raise_auth_error()


def raise_auth_error() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )
