"""Middleware de autenticação por token."""

from __future__ import annotations

import hmac

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.utils.logging import get_logger

logger = get_logger("api.auth")

# Rotas que não requerem autenticação
PUBLIC_PATHS = frozenset({"/health", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc"})


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Ignora autenticacão em endpoints públicos
        if path in PUBLIC_PATHS:
            return await call_next(request)

        settings = get_settings()
        expected_token = settings.api_token

        # Verifica cabeçalho Authorization
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided_token = auth_header[7:]
        else:
            # Também aceita o cabeçalho X-API-Token como alternativa
            provided_token = request.headers.get("X-API-Token", "")

        if not provided_token:
            logger.warning("Missing auth token from %s", request.client.host if request.client else "unknown")
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        if not hmac.compare_digest(provided_token, expected_token):
            logger.warning("Invalid auth token from %s", request.client.host if request.client else "unknown")
            return JSONResponse(status_code=403, content={"detail": "Invalid token"})

        return await call_next(request)
