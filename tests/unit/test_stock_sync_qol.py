"""Unit tests for stock synchronization QOL features: jitter and auto-disabling broken products."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from resell.alerts import DiscordAlerts
from resell.config import MappingConfig, VariantMapping
from resell.models import Variant
from resell.stock_sync import StockSynchronizer


@pytest.fixture
def test_mapping() -> MappingConfig:
    """Fixture providing two mapped product variants."""
    return MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=101,
                our_product_id=201,
                name="Healthy Product",
                supplier_domain="supplier.com",
                supplier_product_slug="healthy-item",
                supplier_variant_id="sup_healthy",
                min_margin_usd=Decimal("1.50"),
                max_price_usd=Decimal("5.00"),
                enabled=True,
            ),
            VariantMapping(
                our_variant_id=102,
                our_product_id=202,
                name="Broken Product",
                supplier_domain="supplier.com",
                supplier_product_slug="broken-item",
                supplier_variant_id="sup_broken",
                min_margin_usd=Decimal("1.50"),
                max_price_usd=Decimal("5.00"),
                enabled=True,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_auto_disable_broken_products_after_3_consecutive_failures(
    test_mapping: VariantMapping,
) -> None:
    """If supplier scraper fails for a variant 3 consecutive times, disable it and alert."""
    store_client = AsyncMock()
    supplier = AsyncMock()
    alerts = AsyncMock(spec=DiscordAlerts)
    broken_var = test_mapping.variants[1]

    # Supplier returns healthy variant, but raises on broken variant
    async def mock_list_variants(domain: str, product_slug: str) -> list[Variant]:
        if product_slug == "broken-item":
            raise RuntimeError("HTTP 404: Supplier Product Not Found")
        return [
            Variant(
                id="sup_healthy",
                name="Healthy Product",
                price=Decimal("3.00"),
                stock=10,
                min_quantity=1,
                max_quantity=10,
            )
        ]

    supplier.list_variants.side_effect = mock_list_variants

    synchronizer = StockSynchronizer(
        mapping=test_mapping,
        store_client=store_client,
        supplier=supplier,
        alerts=alerts,
    )

    # Cycle 1: Broken fails once
    await synchronizer.sync_all()
    assert broken_var.enabled is True
    assert synchronizer.consecutive_failures[102] == 1
    assert not alerts.send_alert.called

    # Cycle 2: Broken fails twice
    await synchronizer.sync_all()
    assert broken_var.enabled is True
    assert synchronizer.consecutive_failures[102] == 2
    assert not alerts.send_alert.called

    # Cycle 3: Broken fails third time -> triggers auto-disable and critical alert
    await synchronizer.sync_all()
    assert broken_var.enabled is False
    assert synchronizer.consecutive_failures[102] == 3

    # Critical alert dispatched
    assert alerts.send_alert.called
    kwargs = alerts.send_alert.call_args.kwargs
    assert kwargs["is_critical"] is True
    assert "Broken Product" in kwargs["title"]

    # Store stock zeroed
    store_client.update_stock.assert_any_call(product_id=202, variant_id=102, stock=0)

    # Cycle 4: Broken is now skipped completely; healthy continues syncing
    store_client.reset_mock()
    alerts.send_alert.reset_mock()
    results = await synchronizer.sync_all()
    assert 101 in results
    assert 102 not in results
    assert not alerts.send_alert.called


@pytest.mark.asyncio
async def test_run_loop_uses_jitter(test_mapping: VariantMapping) -> None:
    """Stock sync run_loop applies +/- 30s jitter to interval."""
    store_client = AsyncMock()
    supplier = AsyncMock()
    supplier.list_variants.return_value = []

    synchronizer = StockSynchronizer(
        mapping=test_mapping,
        store_client=store_client,
        supplier=supplier,
    )

    stop_event = asyncio.Event()

    # Intercept asyncio.sleep or wait_for to check timeout jitter
    captured_timeouts: list[float] = []

    async def mock_wait_for(fut: object, timeout: float) -> None:
        captured_timeouts.append(timeout)
        stop_event.set()

    with patch("asyncio.wait_for", side_effect=mock_wait_for):
        await synchronizer.run_loop(interval_minutes=5.0, stop_event=stop_event)

    assert len(captured_timeouts) == 1
    timeout = captured_timeouts[0]
    # Base is 5 * 60 = 300 seconds. With +/- 30s jitter: 270 <= timeout <= 330
    assert 270.0 <= timeout <= 330.0
