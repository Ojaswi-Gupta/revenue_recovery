"""
RecovrAI — Main Application Entry Point
FastAPI application with all routes, middleware, and lifecycle management.
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .models.database import close_db, init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    settings = get_settings()
    logger.info("=" * 60)
    logger.info(f"  RecovrAI v{settings.app_version} starting up...")
    logger.info(f"  Environment: {settings.app_env.value}")
    logger.info(f"  Demo Mode: {settings.is_demo_mode}")
    logger.info(f"  Groq API: {'✅ Configured' if settings.has_groq else '❌ Not configured'}")
    logger.info(f"  Gemini API: {'✅ Configured' if settings.has_gemini else '❌ Not configured'}")
    logger.info(f"  Twilio: {'✅ Configured' if settings.has_twilio else '❌ Not configured'}")
    logger.info("=" * 60)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    yield

    # Cleanup
    await close_db()
    logger.info("RecovrAI shut down cleanly")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="RecovrAI",
        description=(
            "AI Revenue Recovery Agent — Detects revenue at risk, diagnoses root causes, "
            "and executes bounded recovery workflows with full audit trails."
        ),
        version=settings.app_version,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Register routers
    from .api.dashboard import router as dashboard_router
    from .api.metrics import router as metrics_router
    from .api.webhooks import router as webhooks_router

    app.include_router(dashboard_router)
    app.include_router(metrics_router)
    app.include_router(webhooks_router)

    # Try to import voice router (may fail if dependencies not installed)
    try:
        from .api.voice import router as voice_router
        app.include_router(voice_router)
        logger.info("Voice agent routes registered")
    except ImportError as e:
        logger.warning(f"Voice agent routes not available: {e}")

    # Health check
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "version": settings.app_version,
            "demo_mode": settings.is_demo_mode,
        }

    return app


# Create the app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "recovrai.main:app",
        host=settings.host,
        port=settings.port,
        reload=(settings.app_env.value == "development"),
        log_level=settings.log_level.lower(),
    )
