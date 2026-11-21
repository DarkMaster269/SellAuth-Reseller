"""Unit tests for the orders repository, database transactions, and idempotency."""

from __future__ import annotations

import concurrent.futures
from decimal import Decimal
from pathlib import Path

import pytest

from resell.db import Database
from resell.models import CreateOrderInput, CreatePurchaseInput
from resell.orders import (
    DuplicatePurchaseError,
    OrdersRepo,
)
from resell.states import InvalidStateTransitionError, OrderState


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Fixture providing a fresh isolated SQLite database with migrations applied."""
    db_path = tmp_path / "test_orders.db"
    database = Database(db_path)
    database.apply_migrations()
    return database


@pytest.fixture
def repo(db: Database) -> OrdersRepo:
    """Fixture providing an OrdersRepo connected to test database."""
    return OrdersRepo(db)


def test_create_order_if_new_idempotent(repo: OrdersRepo) -> None:
    """Duplicate create_order_if_new calls must return the existing order without duplicates."""
    order_input = CreateOrderInput(
        invoice_id=17506338,
        item_id=16132365,
        product_id=996063,
        variant_id=2092559,
        quantity=2,
        email="buyer@example.com",
        price_paid_usd=Decimal("0.00"),
        currency="USD",
    )

    # First creation
    order1, created1 = repo.create_order_if_new(order_input)
    assert created1 is True
    assert order1.id is not None
    assert order1.invoice_id == 17506338
    assert order1.state == OrderState.RECEIVED

    # Second creation with identical invoice_id
    order2, created2 = repo.create_order_if_new(order_input)
    assert created2 is False
    assert order2.id == order1.id
    assert order2.invoice_id == order1.invoice_id

    # Verify database has exactly 1 order
    all_orders = repo.get_all_orders()
    assert len(all_orders) == 1


def test_50_concurrent_creates_yield_one_row(db: Database) -> None:
    """50 concurrent creation attempts for the same invoice must yield exactly 1 database row."""
    order_input = CreateOrderInput(
        invoice_id=99999999,
        item_id=88888888,
        product_id=777777,
        variant_id=666666,
        quantity=1,
        email="concurrent@example.com",
        price_paid_usd=Decimal("5.00"),
        currency="USD",
    )

    def worker() -> tuple[int, bool]:
        worker_repo = OrdersRepo(db)
        order, created = worker_repo.create_order_if_new(order_input)
        return (order.id, created)

    # Run 50 concurrent workers via ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(worker) for _ in range(50)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Exactly one worker saw created=True
    created_flags = [created for _, created in results]
    assert created_flags.count(True) == 1
    assert created_flags.count(False) == 49

    # All workers returned the same order ID
    order_ids = {order_id for order_id, _ in results}
    assert len(order_ids) == 1

    # Verify database row count
    repo = OrdersRepo(db)
    order = repo.get_order_by_invoice(99999999)
    assert order is not None
    assert len(repo.get_all_orders()) == 1


def test_atomic_transition_and_events(repo: OrdersRepo) -> None:
    """State transition must update order state and insert event atomically."""
    order_input = CreateOrderInput(
        invoice_id=12345,
        item_id=54321,
        product_id=111,
        variant_id=222,
        quantity=1,
        email="test@example.com",
        price_paid_usd=Decimal("10.00"),
        currency="USD",
    )
    order, _ = repo.create_order_if_new(order_input)
    assert order.state == OrderState.RECEIVED

    # Transition to CHECKOUT_CREATED
    repo.transition(
        order_id=order.id,
        to_state=OrderState.CHECKOUT_CREATED,
        reason="Created supplier checkout",
        metadata={"supplier_checkout_id": "chk_abc123"},
    )

    updated = repo.get_order(order.id)
    assert updated is not None
    assert updated.state == OrderState.CHECKOUT_CREATED

    events = repo.get_events(order.id)
    # Initial 'received' event + new 'checkout_created' event
    assert len(events) == 2
    assert events[0].to_state == OrderState.RECEIVED
    assert events[1].from_state == OrderState.RECEIVED
    assert events[1].to_state == OrderState.CHECKOUT_CREATED
    assert events[1].reason == "Created supplier checkout"


def test_illegal_transition_fails_and_rolls_back(repo: OrdersRepo) -> None:
    """Illegal transition must raise and not alter database state or insert events."""
    order_input = CreateOrderInput(
        invoice_id=67890,
        item_id=98765,
        product_id=111,
        variant_id=222,
        quantity=1,
        email="test@example.com",
        price_paid_usd=Decimal("10.00"),
        currency="USD",
    )
    order, _ = repo.create_order_if_new(order_input)
    assert order.state == OrderState.RECEIVED

    # Attempt illegal transition: received -> delivered
    with pytest.raises(InvalidStateTransitionError):
        repo.transition(
            order_id=order.id,
            to_state=OrderState.DELIVERED,
            reason="Skip directly to delivered",
        )

    # State must still be RECEIVED and only initial creation event present
    current = repo.get_order(order.id)
    assert current is not None
    assert current.state == OrderState.RECEIVED
    events = repo.get_events(order.id)
    assert len(events) == 1
    assert events[0].to_state == OrderState.RECEIVED


def test_second_purchase_for_same_order_fails(repo: OrdersRepo) -> None:
    """Attempting to create a second purchase for an order must fail."""
    order_input = CreateOrderInput(
        invoice_id=1001,
        item_id=2002,
        product_id=111,
        variant_id=222,
        quantity=1,
        email="buyer@example.com",
        price_paid_usd=Decimal("1.00"),
        currency="USD",
    )
    order, _ = repo.create_order_if_new(order_input)

    purchase_input = CreatePurchaseInput(
        order_id=order.id,
        supplier_domain="vna.mysellauth.com",
        supplier_product_slug="vna-3m-nitro",
        supplier_variant_id="var_nitro_3m",
        supplier_checkout_id="chk_1",
        ltc_address="ltc1qfakeaddress123456",
        ltc_amount_sats=1250000,
    )

    purchase1 = repo.create_purchase(purchase_input)
    assert purchase1.id is not None
    assert purchase1.order_id == order.id

    # Second purchase attempt must fail loudly
    with pytest.raises(DuplicatePurchaseError):
        repo.create_purchase(purchase_input)


def test_orders_to_resume(repo: OrdersRepo) -> None:
    """orders_to_resume returns all non-terminal orders."""
    order_in1 = CreateOrderInput(
        invoice_id=2001,
        item_id=1,
        product_id=1,
        variant_id=1,
        quantity=1,
        email="a@test.com",
        price_paid_usd=Decimal("1.00"),
        currency="USD",
    )
    order_in2 = CreateOrderInput(
        invoice_id=2002,
        item_id=2,
        product_id=1,
        variant_id=1,
        quantity=1,
        email="b@test.com",
        price_paid_usd=Decimal("1.00"),
        currency="USD",
    )
    order_in3 = CreateOrderInput(
        invoice_id=2003,
        item_id=3,
        product_id=1,
        variant_id=1,
        quantity=1,
        email="c@test.com",
        price_paid_usd=Decimal("1.00"),
        currency="USD",
    )

    o1, _ = repo.create_order_if_new(order_in1)
    o2, _ = repo.create_order_if_new(order_in2)
    o3, _ = repo.create_order_if_new(order_in3)

    order_in4 = CreateOrderInput(
        invoice_id=2004,
        item_id=4,
        product_id=1,
        variant_id=1,
        quantity=1,
        email="d@test.com",
        price_paid_usd=Decimal("1.00"),
        currency="USD",
    )
    order_in5 = CreateOrderInput(
        invoice_id=2005,
        item_id=5,
        product_id=1,
        variant_id=1,
        quantity=1,
        email="e@test.com",
        price_paid_usd=Decimal("1.00"),
        currency="USD",
    )
    o4, _ = repo.create_order_if_new(order_in4)
    o5, _ = repo.create_order_if_new(order_in5)

    # Deliver o2
    repo.transition(o2.id, OrderState.CHECKOUT_CREATED)
    repo.transition(o2.id, OrderState.PAID)
    repo.transition(o2.id, OrderState.FETCHED)
    repo.transition(o2.id, OrderState.DELIVERED)

    # o3 refund_needed -> refunded
    repo.transition(o3.id, OrderState.REFUND_NEEDED)
    repo.transition(o3.id, OrderState.REFUNDED)

    # o4 is in refund_needed (must be excluded from resume)
    repo.transition(o4.id, OrderState.REFUND_NEEDED)

    # o5 is in checkout_created (must be included in resume)
    repo.transition(o5.id, OrderState.CHECKOUT_CREATED)

    resumable = repo.orders_to_resume()
    resumable_ids = [o.id for o in resumable]
    assert o1.id in resumable_ids  # RECEIVED
    assert o5.id in resumable_ids  # CHECKOUT_CREATED
    assert o2.id not in resumable_ids  # DELIVERED
    assert o3.id not in resumable_ids  # REFUNDED
    assert o4.id not in resumable_ids  # REFUND_NEEDED
    assert len(resumable) == 2


def test_auto_migration_on_startup_or_deleted_db(tmp_path: Path) -> None:
    """If database file is deleted while running, connect() automatically recreates tables."""
    db_path = tmp_path / "deleted_recreated.db"
    database = Database(db_path, auto_migrate=True)
    repo = OrdersRepo(database)

    # 1. Insert an order to confirm tables exist
    order_in = CreateOrderInput(
        invoice_id=8881,
        item_id=1,
        product_id=1,
        variant_id=1,
        quantity=1,
        email="test@example.com",
        price_paid_usd=Decimal("1.00"),
        currency="USD",
    )
    order1, created1 = repo.create_order_if_new(order_in)
    assert created1 is True
    assert order1.id is not None

    # 2. Simulate user deleting the SQLite DB file entirely
    if db_path.exists():
        db_path.unlink()
    # Also clean up WAL and SHM if present
    wal = Path(f"{db_path}-wal")
    if wal.exists():
        wal.unlink()
    shm = Path(f"{db_path}-shm")
    if shm.exists():
        shm.unlink()

    assert not db_path.exists()

    # 3. Next connection should auto-migrate without 'no such table: orders' OperationalError
    order2, created2 = repo.create_order_if_new(order_in)
    assert created2 is True
    assert order2.id is not None

