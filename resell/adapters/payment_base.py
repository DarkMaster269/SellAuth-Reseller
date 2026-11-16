"""Payment adapter protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PaymentAdapter(Protocol):
    """Protocol for cryptocurrency payment operations."""

    async def balance_sats(self) -> int:
        """Return available wallet balance in Satoshis."""
        ...

    async def pay(self, address: str, amount_sats: int, order_id: int) -> str:
        """Broadcast a Litecoin transaction and return the transaction ID (txid)."""
        ...

