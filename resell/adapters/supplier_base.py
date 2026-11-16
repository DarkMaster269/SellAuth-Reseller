"""Supplier adapter protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from resell.models import CheckoutRef, Variant


@runtime_checkable
class SupplierAdapter(Protocol):
    """Protocol for supplier storefront operations."""

    async def list_variants(self, domain: str, product_slug: str) -> list[Variant]:
        """Fetch available variants, prices, and stock from supplier."""
        ...

    async def create_checkout(
        self,
        domain: str,
        product_slug: str,
        variant_id: str,
        quantity: int,
    ) -> CheckoutRef:
        """Create checkout invoice on supplier store and return payment details."""
        ...

    async def lock_payment(
        self,
        domain: str,
        checkout_id: str,
        ltc_address: str,
    ) -> None:
        """Lock in chosen payment method / LTC address if required by supplier."""
        ...

    async def get_status(self, domain: str, checkout_id: str) -> str:
        """Query checkout status ('pending', 'confirming', 'completed', 'cancelled')."""
        ...

    async def fetch_items(self, domain: str, checkout_id: str) -> list[str]:
        """Retrieve delivered items/codes once checkout is completed."""
        ...

