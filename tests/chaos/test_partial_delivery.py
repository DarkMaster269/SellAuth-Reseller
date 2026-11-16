"""Chaos test: Partial or empty supplier delivery."""

from __future__ import annotations

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
async def test_partial_delivery_fails_closed_to_refund_needed(tmp_path: Path) -> None:
    """When supplier delivers fewer items than requested quantity, fail-closed to refund_needed."""
    clock = FakeClock()
    db = Database(tmp_path / "partial.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    # Supplier delivers only 1 item even though 2 were ordered
    supplier = FakeSupplier(clock=clock, confirm_delay_seconds=0.0)
    supplier.partial_delivery = True

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
                max_price_usd=Decimal("20.00"),
                enabled=True,
            )
        ]
    )
    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=tmp_path / "partial.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
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

    # Order requesting 2 items
    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=9003,
            item_id=1,
            product_id=1,
            variant_id=1,
            quantity=2,
            email="buyer@test.com",
            price_paid_usd=Decimal("10.00"),
            currency="USD",
        )
    )

    runner.submit(order.id)
    await runner.process_all_pending(timeout_seconds=5.0)

    # Should transition to REFUND_NEEDED because only 1 of 2 items was delivered
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.REFUND_NEEDED
    assert len(delivery.deliveries) == 0


@pytest.mark.asyncio
async def test_empty_delivery_fails_closed_to_refund_needed(tmp_path: Path) -> None:
    """When supplier delivers 0 items, fail-closed to refund_needed."""
    clock = FakeClock()
    db = Database(tmp_path / "empty.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock, confirm_delay_seconds=0.0)
    supplier.empty_delivery = True

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
        SQLITE_PATH=tmp_path / "empty.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
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
            invoice_id=9004,
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
    await runner.process_all_pending(timeout_seconds=5.0)

    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.REFUND_NEEDED
    assert len(delivery.deliveries) == 0
