"""
FastAPI Application Entry Point
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.logging import logger
from app.modules.agents.agents_routes import router as agents_router
from app.modules.agents.agents_services import AgentService
from app.modules.evaluation.evaluation_routes import router as evaluation_router
from app.modules.execution.execution_routes import execution_router
from app.modules.metadata.metadata_routes import router as metadata_router
from app.modules.migration_plans.migration_plans_routes import router as migration_plans_router
from app.modules.observability.observability_routes import router as observability_router
from app.modules.sources.sources_routes import router as sources_router
from app.modules.users.users_routes import router as users_router

# Import all domain models to ensure SQLAlchemy mappers are registered
import app.modules.agents.agents_models  # noqa: F401
import app.modules.evaluation.evaluation_models  # noqa: F401
import app.modules.execution.execution_models  # noqa: F401
import app.modules.metadata.metadata_models  # noqa: F401
import app.modules.observability.observability_models  # noqa: F401
import app.modules.sources.sources_models  # noqa: F401
import app.modules.migration_plans.migration_plans_models  # noqa: F401
import app.modules.users.users_models  # noqa: F401



async def stale_agent_watchdog():
    """Background periodic loop that detects timed-out agents and recovers orphaned migration jobs."""
    while True:
        try:
            await asyncio.sleep(20)
            async with AsyncSessionLocal() as session:
                try:
                    await AgentService.check_stale_agents_and_jobs(
                        session, stale_threshold_seconds=60
                    )
                except Exception as exc:
                    logger.warning(f"Error in stale agent watchdog run: {exc}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Unexpected error in stale watchdog loop: {e}")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern application lifespan context manager managing background tasks & cleanup."""
    logger.info(f"Starting {settings.PROJECT_NAME} in environment: {settings.ENVIRONMENT}")
    watchdog_task = asyncio.create_task(stale_agent_watchdog())
    yield
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass
    logger.info(f"Shutting down {settings.PROJECT_NAME}...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Register API Routers
app.include_router(sources_router, prefix=settings.API_V1_STR)
app.include_router(users_router, prefix=settings.API_V1_STR)
app.include_router(execution_router, prefix=settings.API_V1_STR)
app.include_router(agents_router, prefix=settings.API_V1_STR)
app.include_router(metadata_router, prefix=settings.API_V1_STR)
app.include_router(migration_plans_router, prefix=settings.API_V1_STR)
app.include_router(observability_router, prefix=settings.API_V1_STR)
app.include_router(evaluation_router, prefix=settings.API_V1_STR)


# CORS Middleware Setup
if "*" in settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://.*$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


from fastapi import Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.core.credential_sanitizer import CredentialSanitizer


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global catch-all exception handler to sanitize error messages,
    ensuring internal credentials, database URIs, and raw stacks are never leaked.
    """
    raw_error = str(exc)
    sanitized_error = CredentialSanitizer.mask_credentials(raw_error)
    logger.error(
        f"Unhandled exception in {request.method} {request.url.path}: {sanitized_error}",
        exc_info=False,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "An unexpected internal server error occurred.",
            "error_detail": (
                sanitized_error
                if settings.ENVIRONMENT.lower() in ("development", "test", "local")
                else "Internal server error"
            ),
        },
    )


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}",
        "docs": "/docs",
        "health": f"{settings.API_V1_STR}/health",
    }


@app.get(f"{settings.API_V1_STR}/health")
async def health_check():
    """
    Active liveness and readiness probe: validates control-plane database connectivity.
    Returns HTTP 200 when healthy, or HTTP 503 Service Unavailable when the database is unreachable.
    """
    db_connected = False
    db_error = None
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            db_connected = True
    except Exception as exc:
        db_error = CredentialSanitizer.mask_credentials(str(exc))
        logger.warning(f"Database health check failed: {db_error}")

    status_code = status.HTTP_200_OK if db_connected else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={
            "success": db_connected,
            "message": "System operational" if db_connected else "Database connectivity degraded",
            "data": {
                "status": "ok" if db_connected else "degraded",
                "version": "0.1.0",
                "environment": settings.ENVIRONMENT,
                "database_connected": db_connected,
                "redis_connected": True,
            },
        },
    )
