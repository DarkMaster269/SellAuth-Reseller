"""Unit tests for stock synchronization formula and synchronizer service."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from resell.adapters.store_client import StoreAuthError
from resell.config import MappingConfig, VariantMapping
from resell.models import Variant
from resell.stock_sync import StockSynchronizer, compute_target_stock


@pytest.mark.parametrize(
    ("supplier_stock", "safety_margin", "in_flight", "expected"),
    [
        (10, 1, 0, 9),    # Normal case: supplier minus safety margin
        (10, 1, 3, 6),    # In-flight orders subtracted
        (0, 1, 0, 0),     # Supplier has zero stock -> store stock is 0
        (2, 2, 1, 0),     # Negative result clamped to 0 (2 - 2 - 1 = -1 -> 0)
        (5, 1, 10, 0),    # Large in-flight count clamped to 0 (5 - 1 - 10 = -6 -> 0)
        (5, 0, 2, 3),     # Zero safety margin
        (3, 2, 1, 0),     # Exact zero boundary (3 - 2 - 1 = 0)
        (-5, 1, 0, 0),    # Negative supplier stock clamped to 0
        (1, 1, 0, 0),     # 1 - 1 - 0 = 0
        (100, 5, 20, 75), # Bulk stock scenario
    ],
)
def test_compute_target_stock_table(
    supplier_stock: int,
    safety_margin: int,
    in_flight: int,
    expected: int,
) -> None:
    """Formula max(0, supplier - safety_margin - in_flight) matches expected values."""
    result = compute_target_stock(
        supplier_stock=supplier_stock,
        safety_margin=safety_margin,
        in_flight=in_flight,
    )
    assert result == expected
    assert result >= 0


@pytest.mark.asyncio
async def test_synchronizer_sync_variant_success() -> None:
    """Synchronizer computes stock and updates store client."""
    variant_map = VariantMapping(
        our_variant_id=2092559,
        our_product_id=996063,
        name="Discord Nitro 3 Months",
        supplier_domain="vna.mysellauth.com",
        supplier_product_slug="vna-3m-nitro",
        supplier_variant_id="1087829",
    )
    mapping = MappingConfig(variants=[variant_map])

    mock_supplier = MagicMock()
    mock_supplier.list_variants = AsyncMock(
        return_value=[
            Variant(
                id="1087829",
                name="Default",
                price=Decimal("0.40"),
                stock=15,
                min_quantity=1,
                max_quantity=None,
            )
        ]
    )

    mock_store_client = MagicMock()
    mock_store_client.update_stock = AsyncMock(return_value=True)

    mock_repo = MagicMock()
    mock_repo.count_in_flight.return_value = 2  # 2 orders in flight

    sync_service = StockSynchronizer(
        mapping=mapping,
        store_client=mock_store_client,
        supplier=mock_supplier,
        orders_repo=mock_repo,
        safety_margin=1,
    )

    # 15 supplier - 1 safety margin - 2 in flight = 12
    synced_stock = await sync_service.sync_variant(variant_map)
    assert synced_stock == 12

    mock_store_client.update_stock.assert_called_once_with(
        product_id=996063,
        variant_id=2092559,
        stock=12,
    )


@pytest.mark.asyncio
async def test_synchronizer_fail_closed_on_supplier_error() -> None:
    """If supplier fails, stock fails-closed to 0 on the store."""
    variant_map = VariantMapping(
        our_variant_id=2092559,
        our_product_id=996063,
        name="Discord Nitro 3 Months",
        supplier_domain="vna.mysellauth.com",
        supplier_product_slug="vna-3m-nitro",
        supplier_variant_id="1087829",
    )
    mapping = MappingConfig(variants=[variant_map])

    mock_supplier = MagicMock()
    mock_supplier.list_variants = AsyncMock(side_effect=RuntimeError("503 challenge failed"))

    mock_store_client = MagicMock()
    mock_store_client.update_stock = AsyncMock(return_value=True)

    sync_service = StockSynchronizer(
        mapping=mapping,
        store_client=mock_store_client,
        supplier=mock_supplier,
        safety_margin=1,
    )

    with pytest.raises(RuntimeError, match="503 challenge failed"):
        await sync_service.sync_variant(variant_map)

    # Must have called update_stock with stock=0
    mock_store_client.update_stock.assert_called_once_with(
        product_id=996063,
        variant_id=2092559,
        stock=0,
    )


@pytest.mark.asyncio
async def test_synchronizer_auth_error_not_swallowed() -> None:
    """If store client auth fails, StoreAuthError is raised and not swallowed."""
    variant_map = VariantMapping(
        our_variant_id=2092559,
        our_product_id=996063,
        name="Discord Nitro 3 Months",
        supplier_domain="vna.mysellauth.com",
        supplier_product_slug="vna-3m-nitro",
        supplier_variant_id="1087829",
    )
    mapping = MappingConfig(variants=[variant_map])

    mock_supplier = MagicMock()
    mock_supplier.list_variants = AsyncMock(
        return_value=[
            Variant(
                id="1087829",
                name="Default",
                price=Decimal("0.40"),
                stock=10,
                min_quantity=1,
                max_quantity=None,
            )
        ]
    )

    mock_store_client = MagicMock()
    mock_store_client.update_stock = AsyncMock(side_effect=StoreAuthError("401 Unauthorized"))

    sync_service = StockSynchronizer(
        mapping=mapping,
        store_client=mock_store_client,
        supplier=mock_supplier,
        safety_margin=1,
    )

    with pytest.raises(StoreAuthError):
        await sync_service.sync_variant(variant_map)
