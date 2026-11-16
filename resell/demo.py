"""Demonstration script simulating normal, slow, and refunded orders."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from resell.adapters.delivery_fake import FakeDelivery
from resell.adapters.payment_fake import FakePayment
from resell.adapters.supplier_fake import FakeSupplier
from resell.clock import FakeClock
from resell.config import MappingConfig, Settings, VariantMapping
from resell.db import Database
from resell.jobs import JobRunner
from resell.models import CreateOrderInput
from resell.orders import OrdersRepo


async def run_simulation() -> None:
    """Run simulated normal, slow, and refunded orders and print event logs."""
    clock = FakeClock()
    demo_db_path = Path("demo_simulation.db")
    if demo_db_path.exists():
        demo_db_path.unlink()

    db = Database(demo_db_path)
    db.apply_migrations()
    repo = OrdersRepo(db)

    # 1. Normal Order Simulation
    supplier_normal = FakeSupplier(clock=clock, confirm_delay_seconds=0.0)
    payment = FakePayment(initial_balance_sats=100000000)
    delivery = FakeDelivery()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=101,
                our_product_id=1,
                name="Discord Nitro 3M",
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
        SELLAUTH_API_KEY="sk_test_demo",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="demo_sec",
        SQLITE_PATH=demo_db_path,
        MAPPING_FILE=Path("mapping.yaml"),
        DELAYED_THRESHOLD_SECONDS=180,
        GIVEUP_THRESHOLD_SECONDS=1800,
    )

    runner_normal = JobRunner(
        repo=repo,
        supplier=supplier_normal,
        payment=payment,
        delivery=delivery,
        mapping=mapping,
        settings=settings,
        clock=clock,
        poll_interval_seconds=0.01,
    )

    print("=" * 70)
    print("RESELL-SELLAUTH SIMULATION DEMO")
    print("=" * 70)

    # Order 1: Normal
    o1, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=10001,
            item_id=1,
            product_id=1,
            variant_id=101,
            quantity=1,
            email="normal_buyer@example.com",
            price_paid_usd=Decimal("6.00"),
            currency="USD",
        )
    )
    runner_normal.submit(o1.id)
    await runner_normal.process_all_pending(timeout_seconds=2.0)

    # Order 2: Slow (delayed confirmation)
    supplier_slow = FakeSupplier(clock=clock, confirm_delay_seconds=250.0)
    runner_slow = JobRunner(
        repo=repo,
        supplier=supplier_slow,
        payment=payment,
        delivery=delivery,
        mapping=mapping,
        settings=settings,
        clock=clock,
        poll_interval_seconds=0.01,
    )

    o2, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=10002,
            item_id=2,
            product_id=1,
            variant_id=101,
            quantity=1,
            email="slow_buyer@example.com",
            price_paid_usd=Decimal("6.00"),
            currency="USD",
        )
    )
    runner_slow.submit(o2.id)
    await asyncio.sleep(0.05)
    # Fast forward clock past delay threshold
    clock.advance(seconds=190.0)
    await asyncio.sleep(0.05)
    # Fast forward clock past confirm delay
    clock.advance(seconds=70.0)
    await runner_slow.process_all_pending(timeout_seconds=2.0)

    # Order 3: Refund Needed (guard breach - price paid below margin floor)
    o3, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=10003,
            item_id=3,
            product_id=1,
            variant_id=101,
            quantity=1,
            email="underpaid_buyer@example.com",
            price_paid_usd=Decimal("1.00"),  # Below margin floor
            currency="USD",
        )
    )
    runner_normal.submit(o3.id)
    await runner_normal.process_all_pending(timeout_seconds=2.0)

    # Print summary of all 3 orders
    for order in [o1, o2, o3]:
        latest = repo.get_order(order.id)
        assert latest is not None
        events = repo.get_events(order.id)
        print(f"\nOrder #{latest.id} (Invoice: {latest.invoice_id}, Email: {latest.email})")
        print(f"Final State: {latest.state.value.upper()}")
        print("Audit Event Trail:")
        for idx, ev in enumerate(events, 1):
            from_str = ev.from_state.value if ev.from_state else "START"
            print(f"  [{idx}] {from_str} -> {ev.to_state.value} ({ev.reason or 'No reason'})")

    print("\n" + "=" * 70)
    print("Simulation complete. Cleaning up demo database.")
    print("=" * 70)
    if demo_db_path.exists():
        demo_db_path.unlink()


def main() -> None:
    """CLI Entrypoint for python -m resell.demo."""
    asyncio.run(run_simulation())


if __name__ == "__main__":
    main()

