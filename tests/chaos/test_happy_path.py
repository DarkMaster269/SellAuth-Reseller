"""Chaos test: Happy path order processing received -> delivered."""

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
async def test_happy_path_received_to_delivered(tmp_path: Path) -> None:
    """End-to-end happy path: received -> checkout_created -> paid -> fetched -> delivered."""
    clock = FakeClock()
    db = Database(tmp_path / "happy_path.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock, confirm_delay_seconds=0.0)
    payment = FakePayment(initial_balance_sats=100000000)
    delivery = FakeDelivery()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=2092559,
                our_product_id=996063,
                name="Test Variant",
                supplier_domain="vna.mysellauth.com",
                supplier_product_slug="vna-3m-nitro",
                supplier_variant_id="var_nitro_3m",
                min_margin_usd=Decimal("1.50"),
                max_price_usd=Decimal("10.00"),
                enabled=True,
            )
        ]
    )

    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=tmp_path / "happy_path.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        MAX_SPEND_PER_ORDER_SATS=5000000,
        MAX_SPEND_PER_DAY_SATS=50000000,
        MIN_WALLET_BALANCE_SATS=10000000,
        MARGIN_FLOOR_USD=Decimal("1.50"),
        DELAYED_THRESHOLD_SECONDS=180,
        GIVEUP_THRESHOLD_SECONDS=1800,
        ELECTRUM_RPC_URL="http://127.0.0.1:7773",
        ELECTRUM_RPC_USER="user",
        ELECTRUM_RPC_PASSWORD="pwd",
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

    order_in = CreateOrderInput(
        invoice_id=5001,
        item_id=1,
        product_id=996063,
        variant_id=2092559,
        quantity=1,
        email="buyer@test.com",
        price_paid_usd=Decimal("5.00"),  # supplier is 3.00, profit is 2.00 >= 1.50 floor
        currency="USD",
    )
    order, _ = repo.create_order_if_new(order_in)

    # Submit to runner and wait for completion
    runner.submit(order.id)
    await runner.process_all_pending(timeout_seconds=5.0)

    # 1. Assert order terminal state
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.DELIVERED

    # 2. Assert events recorded in exact order
    events = repo.get_events(order.id)
    state_sequence = [e.to_state for e in events]
    assert state_sequence == [
        OrderState.RECEIVED,
        OrderState.CHECKOUT_CREATED,
        OrderState.PAID,
        OrderState.FETCHED,
        OrderState.DELIVERED,
    ]

    # 3. Assert purchase record
    purchase = repo.get_purchase(order.id)
    assert purchase is not None
    assert purchase.txid is not None
    assert purchase.items_delivered == ["FAKE-KEY-fake_chk_1-1"]

    # 4. Assert delivery call
    assert len(delivery.deliveries) == 1
    assert delivery.deliveries[0]["order_id"] == order.id
    assert delivery.deliveries[0]["items"] == ["FAKE-KEY-fake_chk_1-1"]
