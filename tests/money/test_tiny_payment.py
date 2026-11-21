"""Money integration test sending a tiny real Litecoin payment to verify wallet operations.

Requires:
  ALLOW_MONEY_TESTS=1 environment variable
  Running Electrum-LTC daemon with unlocked wallet
  (Optional) TEST_LTC_DESTINATION_ADDRESS set to your own wallet receiving address.
"""

from __future__ import annotations

import os

import pytest

from resell.adapters.payment_electrum import ElectrumPaymentAdapter
from resell.config import load_settings


@pytest.mark.money
@pytest.mark.asyncio
async def test_tiny_payment_real_ltc() -> None:
    """Verify wallet connection, query balance, and optionally broadcast a micro-payment."""
    settings = load_settings()

    adapter = ElectrumPaymentAdapter(
        rpc_url=settings.ELECTRUM_RPC_URL,
        rpc_user=settings.ELECTRUM_RPC_USER,
        rpc_password=settings.ELECTRUM_RPC_PASSWORD,
    )

    # 1. Query live balance
    balance = await adapter.balance_sats()
    assert balance >= 0, "Wallet balance query failed or returned negative balance."

    destination = os.getenv("TEST_LTC_DESTINATION_ADDRESS")
    if not destination:
        pytest.skip(
            f"TEST_LTC_DESTINATION_ADDRESS not set. Balance is {balance} sats."
        )

    # 2. Perform tiny test payment (50,000 sats = 0.0005 LTC ~ a few cents)
    tiny_amount_sats = 50000
    assert balance >= tiny_amount_sats, f"Insufficient balance ({balance} sats) for test payment."

    txid = await adapter.pay(
        address=destination,
        amount_sats=tiny_amount_sats,
        order_id=999999,
    )

    assert txid is not None
    assert len(txid) == 64
    print(f"\n[MONEY TEST SUCCESS] Real payment broadcast! TXID: {txid}")
