"""FastAPI application factory with lifespan hooks and route mounting."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from resell import __version__
from resell.config import MappingConfig, Settings, load_mapping, load_settings
from resell.db import Database
from resell.jobs import JobRunnerProtocol, StubJobRunner
from resell.orders import OrdersRepo
from resell.webhook import create_webhook_handler


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    repo: OrdersRepo | None = None,
    runner: JobRunnerProtocol | None = None,
    mapping: MappingConfig | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app_settings = settings or load_settings()
    app_db = db or Database(app_settings.SQLITE_PATH)
    app_db.apply_migrations()
    app_repo = repo or OrdersRepo(app_db)
    app_runner = runner or StubJobRunner()

    app_mapping: MappingConfig | None = mapping
    if app_mapping is None and app_settings.MAPPING_FILE.is_file():
        try:
            app_mapping = load_mapping(app_settings.MAPPING_FILE)
        except Exception:
            app_mapping = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Lifespan manager to resume active jobs on startup."""
        if hasattr(app_runner, "start"):
            await app_runner.start()
        if hasattr(app_runner, "resume"):
            await app_runner.resume()
        yield
        if hasattr(app_runner, "stop"):
            await app_runner.stop()

    app = FastAPI(
        title="resell-sellauth",
        version=__version__,
        description="Automated reselling for SellAuth stores.",
        lifespan=lifespan,
    )

    # Health check endpoint
    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint exposing service availability and version."""
        return {"status": "ok", "version": __version__}

    # Mount webhook router
    webhook_router = create_webhook_handler(
        settings=app_settings,
        repo=app_repo,
        runner=app_runner,
        mapping=app_mapping,
    )
    app.include_router(webhook_router)

    return app


# Default app instance for ASGI servers
app = create_app()
