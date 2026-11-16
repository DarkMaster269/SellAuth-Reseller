"""Configurable in-memory payment fake for chaos testing."""

from __future__ import annotations

import asyncio
from typing import Any


class FakePayment:
    """Deterministic payment fake tracking payments and enforcing single-thread execution."""

    def __init__(self, initial_balance_sats: int = 100000000) -> None:
        self._balance = initial_balance_sats
        self.payments: list[dict[str, Any]] = []
        self._seq = 0
        self.fail_on_pay = False

        # Concurrency verification
        self._active_pay_calls = 0
        self.max_concurrent_pay_calls = 0

    async def balance_sats(self) -> int:
        """Return fake wallet balance."""
        return self._balance

    def set_balance(self, sats: int) -> None:
        """Set fake wallet balance directly."""
        self._balance = sats

    async def pay(self, address: str, amount_sats: int, order_id: int) -> str:
        """Simulate paying LTC to an address."""
        self._active_pay_calls += 1
        self.max_concurrent_pay_calls = max(
            self.max_concurrent_pay_calls,
            self._active_pay_calls,
        )

        try:
            # Yield control to detect any potential concurrency interleaving
            await asyncio.sleep(0.001)

            if self.fail_on_pay:
                raise RuntimeError("Fake payment broadcast failed.")

            if amount_sats > self._balance:
                raise RuntimeError(
                    f"Insufficient funds: required {amount_sats} sats, available {self._balance}"
                )

            self._balance -= amount_sats
            self._seq += 1
            txid = f"fake_ltc_txid_{self._seq:05d}_{order_id}"

            self.payments.append(
                {
                    "txid": txid,
                    "address": address,
                    "amount_sats": amount_sats,
                    "order_id": order_id,
                }
            )
            return txid
        finally:
            self._active_pay_calls -= 1

