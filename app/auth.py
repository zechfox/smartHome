"""Bearer token authentication."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Header, HTTPException, status


def make_token_dependency(token: str) -> Callable[..., Awaitable[None]]:
    async def require_token(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        provided = authorization[7:].strip()
        if not secrets.compare_digest(provided, token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_token
