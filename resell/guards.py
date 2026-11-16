"""Safety guards enforcing margin floors, wallet balances, and spend caps per SECURITY.md."""

from __future__ import annotations

from decimal import Decimal


class GuardBreachError(RuntimeError):
    """Raised when a safety invariant or spend guard is violated."""


def check_margin(
    sale_price_usd: Decimal,
    supplier_cost_usd: Decimal,
    min_margin_usd: Decimal,
) -> bool:
    """Ensure sale price minus supplier cost meets or exceeds margin floor."""
    profit = sale_price_usd - supplier_cost_usd
    return profit >= min_margin_usd


def check_balance(
    wallet_balance_sats: int,
    required_sats: int,
    min_wallet_floor_sats: int,
) -> bool:
    """Ensure remaining balance after purchase meets or exceeds wallet float floor."""
    remaining = wallet_balance_sats - required_sats
    return remaining >= min_wallet_floor_sats


def check_per_order_cap(
    required_sats: int,
    max_spend_per_order_sats: int,
) -> bool:
    """Ensure purchase does not exceed maximum allowable satoshis per order."""
    return required_sats <= max_spend_per_order_sats


def check_daily_cap(
    daily_spent_sats: int,
    required_sats: int,
    max_spend_per_day_sats: int,
) -> bool:
    """Ensure cumulative daily spend + current payment does not exceed daily cap."""
    return (daily_spent_sats + required_sats) <= max_spend_per_day_sats


def validate_guards(
    sale_price_usd: Decimal,
    supplier_cost_usd: Decimal,
    margin_floor_usd: Decimal,
    wallet_balance_sats: int,
    required_sats: int,
    min_wallet_floor_sats: int,
    daily_spent_sats: int,
    max_spend_per_order_sats: int,
    max_spend_per_day_sats: int,
) -> None:
    """Run all safety checks and fail loudly if any guard is breached."""
    if not check_margin(sale_price_usd, supplier_cost_usd, margin_floor_usd):
        raise GuardBreachError(
            f"Margin floor not met: sale=${sale_price_usd}, cost=${supplier_cost_usd}, "
            f"min_margin=${margin_floor_usd}"
        )

    if not check_per_order_cap(required_sats, max_spend_per_order_sats):
        raise GuardBreachError(
            f"Order spend cap exceeded: required={required_sats} > max={max_spend_per_order_sats}"
        )

    if not check_daily_cap(daily_spent_sats, required_sats, max_spend_per_day_sats):
        raise GuardBreachError(
            f"Daily spend cap exceeded: today={daily_spent_sats} + required={required_sats} > "
            f"max_daily={max_spend_per_day_sats}"
        )

    if not check_balance(wallet_balance_sats, required_sats, min_wallet_floor_sats):
        raise GuardBreachError(
            f"Insufficient wallet balance: current={wallet_balance_sats}, "
            f"required={required_sats}, floor={min_wallet_floor_sats}"
        )
