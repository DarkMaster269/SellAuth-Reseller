"""Configurable in-memory supplier fake for chaos and unit testing."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from resell.clock import Clock, RealClock
from resell.models import CheckoutRef, Variant


class FakeSupplier:
    """Deterministic fake supplier with configurable errors and timing."""

    def __init__(
        self,
        clock: Clock | None = None,
        default_price_usd: Decimal = Decimal("3.00"),
        default_sats: int = 1250000,
        confirm_delay_seconds: float = 0.0,
    ) -> None:
        self.clock = clock or RealClock()
        self.default_price_usd = default_price_usd
        self.default_sats = default_sats
        self.confirm_delay_seconds = confirm_delay_seconds

        # Controls & fault injection
        self.fail_checkout: bool = False
        self.fail_status: bool = False
        self.fail_fetch: bool = False
        self.empty_delivery: bool = False
        self.partial_delivery: bool = False
        self.never_confirms: bool = False

        # State storage
        self.checkouts: dict[str, dict[str, Any]] = {}
        self.created_checkout_count = 0
        self._checkout_seq = 0

    async def list_variants(self, domain: str, product_slug: str) -> list[Variant]:
        """Return configured fake variants."""
        return [
            Variant(
                id="var_nitro_3m",
                name="3 Months Nitro",
                price=self.default_price_usd,
                stock=100,
                min_quantity=1,
                max_quantity=10,
            )
        ]

    async def create_checkout(
        self,
        domain: str,
        product_slug: str,
        variant_id: str,
        quantity: int,
    ) -> CheckoutRef:
        """Create a mock checkout or raise error if failure injected."""
        if self.fail_checkout:
            raise RuntimeError("Fake supplier checkout creation failed.")

        self._checkout_seq += 1
        checkout_id = f"fake_chk_{self._checkout_seq}"
        ltc_address = f"ltc1qfakeaddress{self._checkout_seq:04d}"

        self.checkouts[checkout_id] = {
            "checkout_id": checkout_id,
            "created_at": self.clock.now(),
            "status": "pending",
            "quantity": quantity,
        }
        self.created_checkout_count += 1

        return CheckoutRef(
            supplier_domain=domain,
            supplier_checkout_id=checkout_id,
            ltc_address=ltc_address,
            ltc_amount_sats=self.default_sats * quantity,
        )

    async def lock_payment(
        self,
        domain: str,
        checkout_id: str,
        ltc_address: str,
    ) -> None:
        """Record lock payment call."""
        if checkout_id in self.checkouts:
            self.checkouts[checkout_id]["locked_address"] = ltc_address

    async def get_status(self, domain: str, checkout_id: str) -> str:
        """Return checkout status, simulating confirmation timing."""
        if self.fail_status:
            raise RuntimeError("Fake supplier get_status failed.")

        if self.never_confirms:
            return "pending"

        chk = self.checkouts.get(checkout_id)
        if not chk:
            return "not_found"

        elapsed = (self.clock.now() - chk["created_at"]).total_seconds()
        if elapsed >= self.confirm_delay_seconds:
            return "completed"
        return "pending"

    async def fetch_items(self, domain: str, checkout_id: str) -> list[str]:
        """Return fake deliverable keys/items."""
        if self.fail_fetch:
            raise RuntimeError("Fake supplier fetch_items failed.")

        if self.empty_delivery:
            return []

        chk = self.checkouts.get(checkout_id, {})
        qty = chk.get("quantity", 1)

        if self.partial_delivery:
            return [f"FAKE-KEY-{checkout_id}-1"]  # Only 1 item even if qty > 1

        return [f"FAKE-KEY-{checkout_id}-{i}" for i in range(1, qty + 1)]
