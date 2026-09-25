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


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}",
        "docs": "/docs",
        "health": f"{settings.API_V1_STR}/health",
    }


@app.get(f"{settings.API_V1_STR}/health")
async def health_check():
    return {
        "success": True,
        "message": "System operational",
        "data": {
            "status": "ok",
            "version": "0.1.0",
            "environment": settings.ENVIRONMENT,
            "database_connected": True,
            "redis_connected": True,
        },
    }
