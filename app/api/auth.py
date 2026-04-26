"""Autenticação por token para rotas sensíveis."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.utils.logging import get_logger

logger = get_logger("api.auth")

# Rotas que não requerem autenticação
PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/healthz",
        "/ready",
        "/readyz",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)

AUTH_HEADER = "Authorization"
TOKEN_HEADER = "X-Agent-Router-Token"
LEGACY_TOKEN_HEADER = "X-API-Token"


def _extract_token(request: Request) -> str:
    auth_header = request.headers.get(AUTH_HEADER, "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    token = request.headers.get(TOKEN_HEADER, "")
    if token:
        return token

    return request.headers.get(LEGACY_TOKEN_HEADER, "")


def _auth_error(message: str, code: int) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if code == status.HTTP_401_UNAUTHORIZED else None
    return HTTPException(status_code=code, detail=message, headers=headers)


async def require_api_token(request: Request) -> None:
    """Validate the shared API token when one is configured."""
    expected_token = get_settings().api_token
    if not expected_token:
        return

    provided_token = _extract_token(request)
    if not provided_token:
        logger.warning("Missing auth token from %s", request.client.host if request.client else "unknown")
        raise _auth_error("Authentication required", status.HTTP_401_UNAUTHORIZED)

    if not hmac.compare_digest(provided_token, expected_token):
        logger.warning("Invalid auth token from %s", request.client.host if request.client else "unknown")
        raise _auth_error("Invalid token", status.HTTP_403_FORBIDDEN)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Backward-compatible middleware wrapper for token auth."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        try:
            await require_api_token(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

        return await call_next(request)
