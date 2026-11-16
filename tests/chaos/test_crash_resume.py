"""Chaos test: Crash and resume after payment without double-spending."""

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
from resell.models import CreateOrderInput, CreatePurchaseInput
from resell.orders import OrdersRepo
from resell.states import OrderState


@pytest.mark.asyncio
async def test_crash_after_pay_resumes_without_second_payment(tmp_path: Path) -> None:
    """If process crashes after paying LTC, resume() completes delivery without paying again."""
    clock = FakeClock()
    db = Database(tmp_path / "crash.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock, confirm_delay_seconds=0.0)
    payment = FakePayment(initial_balance_sats=50000000)
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
        SQLITE_PATH=tmp_path / "crash.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
    )

    # 1. Simulate state right after a crash: Order is PAID, purchase exists with txid
    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=8888,
            item_id=1,
            product_id=1,
            variant_id=1,
            quantity=1,
            email="buyer@test.com",
            price_paid_usd=Decimal("5.00"),
            currency="USD",
        )
    )
    repo.transition(order.id, OrderState.CHECKOUT_CREATED)

    # Setup checkout on fake supplier so fetch succeeds
    checkout_ref = await supplier.create_checkout("vna.mysellauth.com", "item", "var_1", 1)

    repo.create_purchase(
        CreatePurchaseInput(
            order_id=order.id,
            supplier_domain="vna.mysellauth.com",
            supplier_product_slug="item",
            supplier_variant_id="var_1",
            supplier_checkout_id=checkout_ref.supplier_checkout_id,
            ltc_address=checkout_ref.ltc_address,
            ltc_amount_sats=checkout_ref.ltc_amount_sats,
            txid="already_broadcasted_txid_12345",
        )
    )
    repo.transition(order.id, OrderState.PAID)

    # 2. Boot fresh JobRunner simulating process restart
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

    # Resume active orders
    await runner.resume()
    await runner.process_all_pending(timeout_seconds=5.0)

    # 3. Assert NO new payments were made
    assert len(payment.payments) == 0

    # 4. Assert order was delivered
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.DELIVERED
    assert len(delivery.deliveries) == 1
