"""AIOps Orchestrator - FastAPI application entry point."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.api.metrics import router as metrics_router
from app.api.legacy_usage import LEGACY_DEPRECATION_WARNING, legacy_endpoint_label, record_legacy_endpoint_use
from app.agent_router.main import router as aiops_router, init_catalog_on_startup, get_catalog_readiness
from app.core.config import get_settings
from app.models.database import init_db
from app.services.provider_registry import get_registry
from app.utils.logging import setup_logging, get_logger

_start_time: datetime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    setup_logging()
    logger = get_logger("main")
    settings = get_settings()

    logger.info("Starting AIOps Orchestrator v%s", settings.app_version)
    logger.info("Policy mode: %s", settings.policy_mode)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Initialize providers
    registry = get_registry()
    logger.info("Provider registry initialized")

    # Validate and cache the action catalog
    init_catalog_on_startup()
    catalog_info = get_catalog_readiness()
    if catalog_info["status"] == "ok":
        logger.info("Action catalog loaded: %d actions", catalog_info["actions_count"])
    else:
        logger.error(
            "Action catalog failed to load — readiness will be degraded. "
            "Fix config/actions.yaml and restart."
        )

    _start_time = datetime.now(timezone.utc)
    logger.info("AIOps Orchestrator ready on %s:%d", settings.host, settings.port)

    yield

    logger.info("Shutting down AIOps Orchestrator")


def create_app() -> FastAPI:
    settings = get_settings()
    app_logger = get_logger("main")

    app = FastAPI(
        title="AIOps Orchestrator",
        description="AI-powered homelab orchestration with safety controls",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # CORS - restrict to local network and keep preflight behavior simple.
    _extra_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://chat.ks-sm.net"] + _extra_origins,
        allow_origin_regex=(
            r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
            r"|^http://192\.168\.3\.\d{1,3}(:\d+)?$"
        ),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(api_router)
    app.include_router(aiops_router)
    app.include_router(metrics_router)

    @app.middleware("http")
    async def legacy_deprecation_middleware(request: Request, call_next):
        response = await call_next(request)
        legacy_label = legacy_endpoint_label(request.url.path)
        if legacy_label:
            response.headers.setdefault("Deprecation", "true")
            response.headers.setdefault("Warning", LEGACY_DEPRECATION_WARNING)
            record_legacy_endpoint_use(legacy_label)
            app_logger.warning(
                "Legacy AIOps endpoint used: %s %s",
                request.method,
                request.url.path,
                extra={"legacy_endpoint": legacy_label},
            )
        return response

    # Health endpoints (public, no auth needed)
    @app.get("/health")
    @app.get("/healthz")
    async def health():
        return {
            "status": "healthy",
            "service": "aiops-orchestrator",
            "version": settings.app_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/ready")
    @app.get("/readyz")
    async def ready():
        """Readiness check - verifies dependencies."""
        from app.models.database import get_engine
        checks: dict[str, bool] = {"database": False, "providers": False, "action_catalog": False}

        # Check DB
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(
                    __import__("sqlalchemy").text("SELECT 1")
                )
            checks["database"] = True
        except Exception:
            pass

        # Check at least one LLM provider
        try:
            registry = get_registry()
            for p in registry.llm_providers.values():
                if p.enabled:
                    checks["providers"] = True
                    break
        except Exception:
            pass

        # Check action catalog (loaded at startup)
        catalog_info = get_catalog_readiness()
        checks["action_catalog"] = catalog_info["status"] == "ok"

        all_ready = all(checks.values())
        return {
            "ready": all_ready,
            "status": "ready" if all_ready else "not_ready",
            "checks": checks,
            "dependencies": {
                "action_catalog": catalog_info,
            },
            "uptime_seconds": (datetime.now(timezone.utc) - _start_time).total_seconds() if _start_time else 0,
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=settings.debug,
    )
