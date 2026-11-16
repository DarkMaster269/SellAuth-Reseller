"""Chaos test: Delayed threshold notifications."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from resell.adapters.delivery_fake import FakeDelivery
from resell.adapters.payment_fake import FakePayment
from resell.adapters.supplier_fake import FakeSupplier
from resell.clock import FakeClock
from resell.config import MappingConfig, Settings, VariantMapping
from resell.db import Database
from resell.jobs import JobRunner
from resell.models import CreateOrderInput
from resell.orders import OrdersRepo
from resell.states import OrderState


@pytest.mark.asyncio
async def test_delayed_threshold_notifies_and_still_delivers(tmp_path: Path) -> None:
    """When confirmation exceeds DELAYED_THRESHOLD_SECONDS, notify and complete on confirm."""
    clock = FakeClock()
    db = Database(tmp_path / "delayed.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    # Supplier takes 200 seconds to confirm (delayed threshold is 180s)
    supplier = FakeSupplier(clock=clock, confirm_delay_seconds=200.0)
    payment = FakePayment()
    delivery = FakeDelivery()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=1,
                our_product_id=1,
                name="Item",
                supplier_domain="vna.mysellauth.com",
                supplier_product_slug="item",
                supplier_variant_id="var_1",
                min_margin_usd=Decimal("1.00"),
                max_price_usd=Decimal("10.00"),
                enabled=True,
            )
        ]
    )
    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=tmp_path / "delayed.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        DELAYED_THRESHOLD_SECONDS=180,
        GIVEUP_THRESHOLD_SECONDS=1800,
    )
    runner = JobRunner(
        repo=repo,
        supplier=supplier,
        payment=payment,
        delivery=delivery,
        mapping=mapping,
        settings=settings,
        clock=clock,
        poll_interval_seconds=0.01,
    )

    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=9001,
            item_id=1,
            product_id=1,
            variant_id=1,
            quantity=1,
            email="buyer@test.com",
            price_paid_usd=Decimal("5.00"),
            currency="USD",
        )
    )

    runner.submit(order.id)

    # Run loop a bit; order should become PAID
    await asyncio.sleep(0.05)
    current = repo.get_order(order.id)
    assert current is not None
    assert current.state == OrderState.PAID

    # Advance clock past delayed threshold (185s)
    clock.advance(seconds=185.0)
    await asyncio.sleep(0.05)

    # Assert delay notification occurred and state is DELAYED
    assert order.id in delivery.delayed_notices
    delayed_order = repo.get_order(order.id)
    assert delayed_order is not None
    assert delayed_order.state == OrderState.DELAYED

    # Advance clock past supplier confirm delay (205s total)
    clock.advance(seconds=25.0)
    await runner.process_all_pending(timeout_seconds=5.0)

    # Order is now delivered
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.DELIVERED
    assert len(delivery.deliveries) == 1
