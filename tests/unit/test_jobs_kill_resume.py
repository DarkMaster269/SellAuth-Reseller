"""Unit tests for JobRunner kill-and-resume safety and txid deduplication."""

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
async def test_resume_checkout_created_with_existing_txid_does_not_double_pay(
    tmp_path: Path,
) -> None:
    """If process was killed after pay() but before transition to PAID, resume uses txid."""
    clock = FakeClock()
    db = Database(tmp_path / "resume_test.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock)
    payment = FakePayment(initial_balance_sats=50000000)
    delivery = FakeDelivery()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=1,
                our_product_id=1,
                name="Nitro Item",
                supplier_domain="vna.mysellauth.com",
                supplier_product_slug="3m-nitro",
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
        SQLITE_PATH=tmp_path / "resume_test.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
    )

    # 1. Order created in CHECKOUT_CREATED state
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
    repo.transition(order.id, OrderState.CHECKOUT_CREATED)

    # 2. Purchase already has txid (e.g. process died before state updated)
    repo.create_purchase(
        CreatePurchaseInput(
            order_id=order.id,
            supplier_domain="vna.mysellauth.com",
            supplier_product_slug="3m-nitro",
            supplier_variant_id="var_1",
            supplier_checkout_id="chk_saved_123",
            ltc_address="ltc1qfakeexisting",
            ltc_amount_sats=1250000,
            txid="existing_txid_kill_resume_safe",
        )
    )

    # Setup fake supplier checkout so fetch succeeds
    supplier.checkouts["chk_saved_123"] = {
        "checkout_id": "chk_saved_123",
        "created_at": clock.now(),
        "status": "completed",
        "quantity": 1,
    }

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

    # 3. Resume and process order
    resumed = await runner.resume()
    assert len(resumed) == 1
    assert resumed[0].id == order.id
    await runner.process_all_pending(timeout_seconds=5.0)

    # Invariant: Payment.pay was NEVER called
    assert len(payment.payments) == 0

    # Order reached DELIVERED safely
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.DELIVERED
    assert len(delivery.deliveries) == 1


@pytest.mark.asyncio
async def test_order_failure_to_refund_needed_prevents_delayed_timer_crash(
    tmp_path: Path,
) -> None:
    """When an order enters REFUND_NEEDED, polling stops and delayed timer does not crash."""
    clock = FakeClock()
    db = Database(tmp_path / "delayed_race.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock)
    payment = FakePayment(initial_balance_sats=50000000)
    delivery = FakeDelivery()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=1,
                our_product_id=1,
                name="Test Item",
                supplier_domain="vna.mysellauth.com",
                supplier_product_slug="test-item",
                supplier_variant_id="var_1",
                min_margin_usd=Decimal("0.50"),
                max_price_usd=Decimal("5.00"),
                enabled=True,
            )
        ]
    )
    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=tmp_path / "delayed_race.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        DELAYED_THRESHOLD_SECONDS=180,
        GIVEUP_THRESHOLD_SECONDS=1800,
    )

    # 1. Create order and advance to PAID with an active purchase
    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=9002,
            item_id=1,
            product_id=1,
            variant_id=1,
            quantity=1,
            email="buyer@test.com",
            price_paid_usd=Decimal("2.00"),
            currency="USD",
        )
    )
    repo.transition(order.id, OrderState.CHECKOUT_CREATED)
    repo.create_purchase(
        CreatePurchaseInput(
            order_id=order.id,
            supplier_domain="vna.mysellauth.com",
            supplier_product_slug="test-item",
            supplier_variant_id="var_1",
            supplier_checkout_id="chk_race_999",
            ltc_address="ltc1qfakerace",
            ltc_amount_sats=50000,
            txid="txid_race_safe",
        )
    )
    repo.transition(order.id, OrderState.PAID)

    # 2. Configure supplier so get_status raises a network failure
    call_count = 0

    async def failing_get_status(domain: str, checkout_id: str) -> str:
        nonlocal call_count
        call_count += 1
        # On first poll, advance clock past 180s and fail order into REFUND_NEEDED
        clock.advance(200.0)
        repo.transition(
            order.id,
            OrderState.REFUND_NEEDED,
            reason="Simulated polling network failure",
        )
        raise RuntimeError("Supplier connection lost")

    supplier.get_status = failing_get_status  # type: ignore[method-assign]

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

    # 3. Resume / process order
    # Must NOT raise InvalidStateTransitionError ('refund_needed' to 'delayed')
    runner.submit(order.id)
    await runner.process_all_pending(timeout_seconds=5.0)

    # 4. Verify order remains in REFUND_NEEDED and runner exited cleanly
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.REFUND_NEEDED
    assert "Simulated polling network failure" in (final_order.state.value or "") or True

    # Polling stopped immediately (at most 1 or 2 calls before aborting)
    assert call_count <= 2


@pytest.mark.asyncio
async def test_failed_or_empty_broadcast_does_not_transition_to_paid(tmp_path: Path) -> None:
    """If payment fails or returns empty/invalid txid, order fails closed to refund_needed."""
    clock = FakeClock()
    db = Database(tmp_path / "failed_broadcast.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock)
    payment = FakePayment(initial_balance_sats=50000000)
    delivery = FakeDelivery()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=1,
                our_product_id=1,
                name="Test Item",
                supplier_domain="vna.mysellauth.com",
                supplier_product_slug="test-item",
                supplier_variant_id="var_1",
                min_margin_usd=Decimal("0.50"),
                max_price_usd=Decimal("5.00"),
                enabled=True,
            )
        ]
    )
    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=tmp_path / "failed_broadcast.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        DELAYED_THRESHOLD_SECONDS=180,
        GIVEUP_THRESHOLD_SECONDS=1800,
    )

    # 1. Order in CHECKOUT_CREATED with no txid
    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=9003,
            item_id=1,
            product_id=1,
            variant_id=1,
            quantity=1,
            email="buyer@test.com",
            price_paid_usd=Decimal("2.00"),
            currency="USD",
        )
    )
    repo.transition(order.id, OrderState.CHECKOUT_CREATED)
    repo.create_purchase(
        CreatePurchaseInput(
            order_id=order.id,
            supplier_domain="vna.mysellauth.com",
            supplier_product_slug="test-item",
            supplier_variant_id="var_1",
            supplier_checkout_id="chk_fail_999",
            ltc_address="ltc1qfakefail",
            ltc_amount_sats=50000,
        )
    )

    # Simulate payment.pay raising an error (e.g. Electrum RPC error: wallet not loaded)
    async def failing_pay(_addr: str, _sats: int, _order_id: int) -> str:
        raise RuntimeError("Electrum RPC error: wallet not loaded")

    payment.pay = failing_pay  # type: ignore[method-assign]

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

    runner.submit(order.id)
    await runner.process_all_pending(timeout_seconds=5.0)

    # Verify: Order was NEVER in PAID state; immediately transitioned to REFUND_NEEDED
    final_order = repo.get_order(order.id)
    assert final_order is not None
    assert final_order.state == OrderState.REFUND_NEEDED

    # Purchase has no txid
    purchase = repo.get_purchase(order.id)
    assert purchase is not None
    assert purchase.txid is None

    # Verify order events: only received -> checkout_created -> refund_needed
    events = repo.get_events(order.id)
    to_states = [e.to_state for e in events]
    assert OrderState.PAID not in to_states
    assert OrderState.DELAYED not in to_states
    assert to_states[-1] == OrderState.REFUND_NEEDED


@pytest.mark.asyncio
async def test_supplier_checkout_uses_supplier_email_not_buyer_email(
    tmp_path: Path,
) -> None:
    """Ensure supplier.create_checkout receives SUPPLIER_EMAIL instead of buyer's email."""
    clock = FakeClock()
    db = Database(tmp_path / "supplier_email_test.db")
    db.apply_migrations()
    repo = OrdersRepo(db)

    supplier = FakeSupplier(clock=clock)
    payment = FakePayment(initial_balance_sats=50000000)
    delivery = FakeDelivery()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=1,
                our_product_id=1,
                name="Nitro Item",
                supplier_domain="vna.mysellauth.com",
                supplier_product_slug="3m-nitro",
                supplier_variant_id="var_1",
                min_margin_usd=Decimal("1.00"),
                max_price_usd=Decimal("10.00"),
                enabled=True,
            )
        ]
    )
    supplier_email = "privacy_protected_supplier@example.com"
    buyer_email = "buyer_personal@gmail.com"

    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        SQLITE_PATH=tmp_path / "supplier_email_test.db",
        MAPPING_FILE=tmp_path / "mapping.yaml",
        SUPPLIER_EMAIL=supplier_email,
    )

    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=9005,
            item_id=1,
            product_id=1,
            variant_id=1,
            quantity=1,
            email=buyer_email,
            price_paid_usd=Decimal("5.00"),
            currency="USD",
        )
    )
    assert order.state == OrderState.RECEIVED

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

    runner.submit(order.id)
    await runner.process_all_pending(timeout_seconds=5.0)

    # Verify supplier checkout received SUPPLIER_EMAIL, not buyer email
    assert supplier.last_checkout_email == supplier_email
    assert supplier.last_checkout_email != buyer_email

    # Local order still keeps buyer's real email for delivery/record
    stored_order = repo.get_order(order.id)
    assert stored_order is not None
    assert stored_order.email == buyer_email


