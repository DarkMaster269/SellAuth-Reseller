"""Chaos test: Give-up threshold handling."""

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
async def test_giveup_threshold_transitions_to_refund_needed_with_txid(tmp_path: Path) -> None:
    """If supplier never confirms and timeout exceeded, transition to refund_needed with txid."""
    clock = FakeClock()
    db = Database(tmp_path / "giveup.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    # Supplier never confirms
    supplier = FakeSupplier(clock=clock)
    supplier.never_confirms = True

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
        SQLITE_PATH=tmp_path / "giveup.db",
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
            invoice_id=9002,
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
    await asyncio.sleep(0.05)

    # Advance clock past delayed threshold (180s)
    clock.advance(seconds=200.0)
    await asyncio.sleep(0.05)

    # Advance clock past give-up threshold (1800s)
    clock.advance(seconds=1700.0)
    await runner.process_all_pending(timeout_seconds=5.0)

    # 1. Order is marked REFUND_NEEDED
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.REFUND_NEEDED

    # 2. Purchase still preserves the broadcasted txid
    purchase = repo.get_purchase(order.id)
    assert purchase is not None
    assert purchase.txid is not None
    assert len(delivery.deliveries) == 0
