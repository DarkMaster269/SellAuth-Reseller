"""FastAPI application factory with lifespan hooks and route mounting."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from resell import __version__
from resell.config import MappingConfig, Settings, load_mapping, load_settings
from resell.db import Database
from resell.jobs import JobRunnerProtocol, StubJobRunner
from resell.logging_setup import setup_logging
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
    setup_logging()
    app_settings = settings or load_settings()
    app_db = db or Database(app_settings.SQLITE_PATH)
    app_db.apply_migrations()
    app_repo = repo or OrdersRepo(app_db)
    app_mapping: MappingConfig | None = mapping
    if app_mapping is None and app_settings.MAPPING_FILE.is_file():
        try:
            app_mapping = load_mapping(app_settings.MAPPING_FILE)
        except Exception:
            app_mapping = None

    if runner is not None:
        app_runner: JobRunnerProtocol = runner
    elif app_mapping is not None:
        from resell.adapters.delivery_late import LateDeliveryAdapter
        from resell.adapters.payment_electrum import ElectrumPaymentAdapter
        from resell.adapters.supplier_scrape import SupplierScraper
        from resell.jobs import JobRunner

        supplier = SupplierScraper()
        payment = ElectrumPaymentAdapter(
            rpc_url=app_settings.ELECTRUM_RPC_URL,
            rpc_user=app_settings.ELECTRUM_RPC_USER,
            rpc_password=app_settings.ELECTRUM_RPC_PASSWORD,
        )
        delivery = LateDeliveryAdapter(
            api_key=app_settings.SELLAUTH_API_KEY.get_secret_value(),
            shop_id=app_settings.SELLAUTH_SHOP_ID,
        )
        app_runner = JobRunner(
            repo=app_repo,
            supplier=supplier,
            payment=payment,
            delivery=delivery,
            mapping=app_mapping,
            settings=app_settings,
        )
    else:
        app_runner = StubJobRunner()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Lifespan manager to ensure migrations run and active jobs resume on startup."""
        import asyncio

        from resell.alerts import DiscordAlerts

        app_db.apply_migrations()
        alerts = DiscordAlerts(app_settings)
        variant_count = len(app_mapping.variants) if app_mapping else 0
        try:
            await alerts.send_startup(variant_count=variant_count)
        except Exception:
            pass

        if hasattr(app_runner, "start"):
            await app_runner.start()
        if hasattr(app_runner, "resume"):
            await app_runner.resume()

        # Background stock sync with jitter
        sync_task: asyncio.Task[None] | None = None
        sync_stop_event = asyncio.Event()
        if app_mapping is not None and getattr(app_settings, "SELLAUTH_API_KEY", None):
            try:
                from resell.adapters.store_client import SellAuthStoreClient
                from resell.adapters.supplier_scrape import SupplierScraper
                from resell.stock_sync import StockSynchronizer

                sync_store = SellAuthStoreClient(
                    api_key=app_settings.SELLAUTH_API_KEY.get_secret_value(),
                    shop_id=app_settings.SELLAUTH_SHOP_ID,
                )
                sync_supp = SupplierScraper()
                synchronizer = StockSynchronizer(
                    mapping=app_mapping,
                    store_client=sync_store,
                    supplier=sync_supp,
                    orders_repo=app_repo,
                    alerts=alerts,
                )
                sync_task = asyncio.create_task(
                    synchronizer.run_loop(
                        interval_minutes=float(app_settings.STOCK_SYNC_INTERVAL_MINUTES),
                        stop_event=sync_stop_event,
                    )
                )
            except Exception as sync_init_err:
                import logging

                logger_sync = logging.getLogger(__name__)
                logger_sync.warning(
                    "Could not start background sync: %s",
                    sync_init_err,
                )

        yield

        # Shutdown sequence
        sync_stop_event.set()
        if sync_task is not None:
            sync_task.cancel()
            try:
                await sync_task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            await alerts.send_shutdown()
        except Exception:
            pass

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
