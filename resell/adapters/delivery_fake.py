"""Configurable in-memory delivery fake for chaos testing."""

from __future__ import annotations

from typing import Any

from resell.models import Order


class FakeDelivery:
    """Deterministic delivery fake tracking deliveries and delayed notices."""

    def __init__(self) -> None:
        self.deliveries: list[dict[str, Any]] = []
        self.delayed_notices: list[int] = []
        self.fail_delivery: bool = False

    async def deliver(self, order: Order, items: list[str]) -> bool:
        """Record delivery of items."""
        if self.fail_delivery:
            raise RuntimeError("Fake delivery failed.")

        self.deliveries.append(
            {
                "order_id": order.id,
                "invoice_id": order.invoice_id,
                "items": items,
            }
        )
        return True

    async def notify_delayed(self, order: Order) -> None:
        """Record delayed notification."""
        self.delayed_notices.append(order.id)

