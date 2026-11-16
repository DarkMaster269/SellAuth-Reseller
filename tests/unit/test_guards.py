"""Unit tests for safety guards (margin, balance floor, spend caps)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from resell.guards import (
    GuardBreachError,
    check_balance,
    check_daily_cap,
    check_margin,
    check_per_order_cap,
    validate_guards,
)


def test_margin_check_passes_when_profit_exceeds_floor() -> None:
    """Margin check passes when sale price minus supplier cost >= margin floor."""
    sale_price = Decimal("10.00")
    supplier_cost = Decimal("7.00")
    floor = Decimal("2.00")  # profit is 3.00 >= 2.00
    assert check_margin(sale_price, supplier_cost, floor) is True


def test_margin_check_fails_when_profit_below_floor() -> None:
    """Margin check fails when profit is less than required floor."""
    sale_price = Decimal("10.00")
    supplier_cost = Decimal("8.50")
    floor = Decimal("2.00")  # profit is 1.50 < 2.00
    assert check_margin(sale_price, supplier_cost, floor) is False


def test_balance_check_enforces_wallet_floor() -> None:
    """Balance check ensures remaining balance after payment remains >= min floor."""
    balance = 20000000        # 0.20 LTC
    payment = 5000000         # 0.05 LTC
    min_floor = 10000000      # 0.10 LTC floor -> remaining is 0.15 LTC >= 0.10 LTC
    assert check_balance(balance, payment, min_floor) is True

    # When remaining drops below floor
    insufficient_balance = 12000000  # remaining 0.07 LTC < 0.10 LTC
    assert check_balance(insufficient_balance, payment, min_floor) is False


def test_per_order_cap_enforced() -> None:
    """Individual payment cannot exceed MAX_SPEND_PER_ORDER_SATS."""
    max_cap = 5000000  # 0.05 LTC
    assert check_per_order_cap(4999999, max_cap) is True
    assert check_per_order_cap(5000000, max_cap) is True
    assert check_per_order_cap(5000001, max_cap) is False


def test_daily_cap_enforced() -> None:
    """Cumulative daily spend + current payment cannot exceed MAX_SPEND_PER_DAY_SATS."""
    daily_cap = 50000000  # 0.50 LTC
    spent_today = 45000000
    assert check_daily_cap(spent_today, 5000000, daily_cap) is True
    assert check_daily_cap(spent_today, 5000001, daily_cap) is False


def test_validate_guards_raises_guard_breach_error() -> None:
    """validate_guards aggregates checks and raises specific GuardBreachError."""
    with pytest.raises(GuardBreachError) as exc_info:
        validate_guards(
            sale_price_usd=Decimal("5.00"),
            supplier_cost_usd=Decimal("4.50"),
            margin_floor_usd=Decimal("1.50"),
            wallet_balance_sats=50000000,
            required_sats=1000000,
            min_wallet_floor_sats=10000000,
            daily_spent_sats=0,
            max_spend_per_order_sats=5000000,
            max_spend_per_day_sats=50000000,
        )
    assert "Margin floor not met" in str(exc_info.value)
