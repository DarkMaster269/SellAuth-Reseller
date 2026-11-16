"""Chaos test: Guard breaches fail closed to refund_needed without paying."""

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
async def test_insufficient_wallet_balance_fails_closed(tmp_path: Path) -> None:
    """When wallet balance drops below required float floor, fail closed without calling pay."""
    clock = FakeClock()
    db = Database(tmp_path / "low_bal.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock)
    # Wallet balance is only 5,000,000 sats, but float floor requires 10,000,000 sats
    payment = FakePayment(initial_balance_sats=5000000)
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
        SQLITE_PATH=tmp_path / "low_bal.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        MIN_WALLET_BALANCE_SATS=10000000,
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
            invoice_id=9005,
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

    # 1. State must fail-closed to REFUND_NEEDED
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.REFUND_NEEDED

    # 2. Payment must NEVER have been called
    assert len(payment.payments) == 0


@pytest.mark.asyncio
async def test_margin_floor_breach_fails_closed(tmp_path: Path) -> None:
    """When customer price minus supplier cost is less than MARGIN_FLOOR_USD, fail closed."""
    clock = FakeClock()
    db = Database(tmp_path / "margin.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    # Supplier costs $3.00
    supplier = FakeSupplier(clock=clock, default_price_usd=Decimal("3.00"))
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
                min_margin_usd=Decimal("2.50"),  # requires $2.50 margin
                max_price_usd=Decimal("10.00"),
                enabled=True,
            )
        ]
    )
    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=tmp_path / "margin.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        MARGIN_FLOOR_USD=Decimal("2.50"),
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

    # Customer only paid $4.00 -> profit is $1.00 < $2.50 floor
    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=9006,
            item_id=1,
            product_id=1,
            variant_id=1,
            quantity=1,
            email="buyer@test.com",
            price_paid_usd=Decimal("4.00"),
            currency="USD",
        )
    )

    runner.submit(order.id)
    await runner.process_all_pending(timeout_seconds=5.0)

    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.REFUND_NEEDED
    assert len(payment.payments) == 0


@pytest.mark.asyncio
async def test_order_spend_cap_breach_fails_closed(tmp_path: Path) -> None:
    """When required payment exceeds MAX_SPEND_PER_ORDER_SATS, fail closed."""
    clock = FakeClock()
    db = Database(tmp_path / "spend_cap.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    # Supplier requests 6,000,000 sats (cap is 5,000,000)
    supplier = FakeSupplier(clock=clock, default_sats=6000000)
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
                max_price_usd=Decimal("100.00"),
                enabled=True,
            )
        ]
    )
    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=tmp_path / "spend_cap.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        MAX_SPEND_PER_ORDER_SATS=5000000,
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
            invoice_id=9007,
            item_id=1,
            product_id=1,
            variant_id=1,
            quantity=1,
            email="buyer@test.com",
            price_paid_usd=Decimal("20.00"),
            currency="USD",
        )
    )

    runner.submit(order.id)
    await runner.process_all_pending(timeout_seconds=5.0)

    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.REFUND_NEEDED
    assert len(payment.payments) == 0
