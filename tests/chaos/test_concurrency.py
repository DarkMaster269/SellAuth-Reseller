"""Chaos test: High concurrency and global payment serialization."""

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
async def test_20_concurrent_orders_execute_with_serialized_payments(tmp_path: Path) -> None:
    """20 concurrent orders create 20 purchases with strictly serialized payments."""
    clock = FakeClock()
    db = Database(tmp_path / "concurrent.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock, confirm_delay_seconds=0.0)
    # Sufficient float for 20 orders (each 1,250,000 sats -> 25,000,000 sats)
    payment = FakePayment(initial_balance_sats=100000000)
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
        SQLITE_PATH=tmp_path / "concurrent.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        MAX_SPEND_PER_ORDER_SATS=5000000,
        MAX_SPEND_PER_DAY_SATS=100000000,
        MIN_WALLET_BALANCE_SATS=5000000,
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

    order_ids = []
    for i in range(20):
        order, _ = repo.create_order_if_new(
            CreateOrderInput(
                invoice_id=7000 + i,
                item_id=i,
                product_id=1,
                variant_id=1,
                quantity=1,
                email=f"buyer{i}@test.com",
                price_paid_usd=Decimal("5.00"),
                currency="USD",
            )
        )
        order_ids.append(order.id)
        runner.submit(order.id)

    # Process all 20 orders
    await runner.process_all_pending(timeout_seconds=10.0)

    # 1. Assert exactly 20 purchases made
    assert len(payment.payments) == 20
    assert len(delivery.deliveries) == 20

    # 2. Assert global lock ensured NO overlapping pay calls
    assert payment.max_concurrent_pay_calls == 1

    # 3. Assert all orders delivered
    for o_id in order_ids:
        o = repo.get_order(o_id)
        assert o is not None
        assert o.state == OrderState.DELIVERED
