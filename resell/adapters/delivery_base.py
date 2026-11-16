"""Delivery adapter protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from resell.models import Order


@runtime_checkable
class DeliveryAdapter(Protocol):
    """Protocol for customer delivery and delay notification operations."""

    async def deliver(self, order: Order, items: list[str]) -> bool:
        """Deliver items to customer via SellAuth invoice delivery API."""
        ...

    async def notify_delayed(self, order: Order) -> None:
        """Send notification to customer that payment confirmation is delayed."""
        ...

