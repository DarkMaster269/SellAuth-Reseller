"""Unit tests for the killswitch and daily report CLI commands."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from resell.cli import run_killswitch, run_report
from resell.config import MappingConfig, Settings, VariantMapping
from resell.db import Database
from resell.jobs import JobRunner
from resell.killswitch import (
    activate_killswitch,
    deactivate_killswitch,
    is_killswitch_active,
)
from resell.models import CreateOrderInput, CreatePurchaseInput
from resell.orders import OrdersRepo
from resell.states import OrderState


@pytest.fixture
def test_setup(tmp_path: Path) -> tuple[Settings, MappingConfig, Database]:
    """Provide isolated environment with SQLite database and mapped variant."""
    db_path = tmp_path / "cli_test.db"
    database = Database(db_path)
    database.apply_migrations()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=501,
                our_product_id=601,
                name="Test Variant",
                supplier_domain="supplier.com",
                supplier_product_slug="test-item",
                supplier_variant_id="sup_501",
                min_margin_usd=Decimal("1.50"),
                max_price_usd=Decimal("5.00"),
                enabled=True,
            )
        ]
    )

    mapping_path = tmp_path / "mapping.yaml"
    mapping_path.write_text(
        """
variants:
  - our_variant_id: 501
    our_product_id: 601
    name: "Test Variant"
    supplier_domain: "supplier.com"
    supplier_product_slug: "test-item"
    supplier_variant_id: "sup_501"
    min_margin_usd: "1.50"
    max_price_usd: "5.00"
    enabled: true
""",
        encoding="utf-8",
    )

    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=db_path,
        MAPPING_FILE=mapping_path,
        DISCORD_WEBHOOK_ALERTS="https://discord.com/api/webhooks/alerts/123",
        DISCORD_WEBHOOK_DELIVERED="https://discord.com/api/webhooks/delivered/456",
    )
    return settings, mapping, database


@pytest.mark.asyncio
async def test_killswitch_engages_zeros_stock_and_alerts(
    test_setup: tuple[Settings, MappingConfig, Database],
) -> None:
    """run_killswitch zeroes all variant stocks, sets lock file, and alerts Discord."""
    settings, mapping, _ = test_setup

    with (
        patch("resell.cli.load_settings", return_value=settings),
        patch("resell.cli.load_mapping", return_value=mapping),
        patch(
            "resell.adapters.store_client.SellAuthStoreClient.update_stock",
            new_callable=AsyncMock,
        ) as mock_zero,
        patch("resell.alerts.DiscordAlerts.send_alert", new_callable=AsyncMock) as mock_alert,
    ):
        # 1. Engage killswitch
        await run_killswitch(resume=False)

        assert is_killswitch_active(settings)
        # Store stock set to 0 for mapped variant
        mock_zero.assert_called_once_with(product_id=601, variant_id=501, stock=0)
        # Critical alert sent
        assert mock_alert.called
        assert mock_alert.call_args.kwargs["is_critical"] is True

        # 2. Disengage killswitch
        await run_killswitch(resume=True)
        assert not is_killswitch_active(settings)


@pytest.mark.asyncio
async def test_job_runner_pauses_when_killswitch_engaged(
    test_setup: tuple[Settings, MappingConfig, Database],
) -> None:
    """JobRunner immediately halts order fulfillment when killswitch is active."""
    settings, mapping, db = test_setup
    repo = OrdersRepo(db)

    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=8801,
            item_id=1,
            product_id=601,
            variant_id=501,
            quantity=1,
            email="buyer@test.com",
            price_paid_usd=Decimal("10.00"),
            currency="USD",
        )
    )

    supplier = AsyncMock()
    payment = AsyncMock()
    delivery = AsyncMock()

    runner = JobRunner(
        repo=repo,
        supplier=supplier,
        payment=payment,
        delivery=delivery,
        mapping=mapping,
        settings=settings,
        poll_interval_seconds=0.01,
    )

    # Engage killswitch
    activate_killswitch(settings)
    try:
        runner.submit(order.id)
        await runner.process_all_pending(timeout_seconds=2.0)

        # Order must remain untouched in RECEIVED state
        stored_order = repo.get_order(order.id)
        assert stored_order is not None
        assert stored_order.state == OrderState.RECEIVED
        # No supplier checkout or payment attempted
        assert not supplier.create_checkout.called
        assert not payment.pay.called
    finally:
        deactivate_killswitch(settings)


@pytest.mark.asyncio
async def test_daily_report_calculation_and_dispatch(
    test_setup: tuple[Settings, MappingConfig, Database],
) -> None:
    """run_report computes accurate totals from SQLite and dispatches to Discord."""
    settings, mapping, db = test_setup
    repo = OrdersRepo(db)

    # Create and deliver Order 1 ($10 revenue, $5 supplier cost)
    o1, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=9901,
            item_id=1,
            product_id=601,
            variant_id=501,
            quantity=1,
            email="buyer1@test.com",
            price_paid_usd=Decimal("10.00"),
            currency="USD",
        )
    )
    repo.transition(o1.id, OrderState.CHECKOUT_CREATED)
    repo.create_purchase(
        CreatePurchaseInput(
            order_id=o1.id,
            supplier_domain="supplier.com",
            supplier_product_slug="test-item",
            supplier_variant_id="sup_501",
            ltc_amount_sats=1250000,
        )
    )
    repo.transition(o1.id, OrderState.PAID)
    repo.transition(o1.id, OrderState.FETCHED)
    repo.transition(o1.id, OrderState.DELIVERED)

    # Create and deliver Order 2 ($15 revenue, $5 supplier cost)
    o2, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=9902,
            item_id=2,
            product_id=601,
            variant_id=501,
            quantity=1,
            email="buyer2@test.com",
            price_paid_usd=Decimal("15.00"),
            currency="USD",
        )
    )
    repo.transition(o2.id, OrderState.CHECKOUT_CREATED)
    repo.create_purchase(
        CreatePurchaseInput(
            order_id=o2.id,
            supplier_domain="supplier.com",
            supplier_product_slug="test-item",
            supplier_variant_id="sup_501",
            ltc_amount_sats=1250000,
        )
    )
    repo.transition(o2.id, OrderState.PAID)
    repo.transition(o2.id, OrderState.FETCHED)
    repo.transition(o2.id, OrderState.DELIVERED)

    with (
        patch("resell.cli.load_settings", return_value=settings),
        patch("resell.cli.load_mapping", return_value=mapping),
        patch(
            "resell.alerts.DiscordAlerts.send_daily_report",
            new_callable=AsyncMock,
        ) as mock_report,
    ):
        total_orders, revenue, cost, profit = await run_report(hours=24)

        assert total_orders == 2
        assert revenue == Decimal("25.00")
        assert cost == Decimal("10.00")  # 2 * $5.00
        assert profit == Decimal("15.00")  # $25.00 - $10.00

        mock_report.assert_called_once_with(
            total_orders=2,
            total_revenue_usd=Decimal("25.00"),
            total_supplier_cost_usd=Decimal("10.00"),
            total_profit_usd=Decimal("15.00"),
        )
