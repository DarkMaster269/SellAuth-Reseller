"""Contract tests for SellAuth Store Client API client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resell.adapters.store_client import SellAuthStoreClient, StoreAuthError


@pytest.mark.asyncio
async def test_update_stock_contract() -> None:
    """PUT /v1/shops/{shopId}/products/{productId}/stock/{variantId} matches documented schema."""
    shop_id = 226299
    product_id = 996063
    variant_id = 2092559
    new_stock = 15

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "stock": new_stock}

    mock_client = MagicMock()
    mock_client.put = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        client = SellAuthStoreClient(api_key="test_api_key", shop_id=shop_id)
        result = await client.update_stock(
            product_id=product_id,
            variant_id=variant_id,
            stock=new_stock,
        )

        assert result is True
        mock_client.put.assert_called_once()
        call_url = mock_client.put.call_args[0][0]
        call_kwargs = mock_client.put.call_args[1]

        assert f"/v1/shops/{shop_id}/products/{product_id}/stock/{variant_id}" in call_url
        assert call_kwargs["json"] == {"stock": new_stock}
        assert call_kwargs["headers"]["Authorization"] == "Bearer test_api_key"


@pytest.mark.asyncio
async def test_deliver_items_contract() -> None:
    """POST /v1/shops/{shopId}/invoices/{invoiceId}/deliver matches documented schema."""
    shop_id = 226299
    invoice_id = 17506338
    items = ["DISCORD-PROMO-TOKEN-ABC", "DISCORD-PROMO-TOKEN-XYZ"]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "delivered": len(items)}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        client = SellAuthStoreClient(api_key="test_api_key", shop_id=shop_id)
        result = await client.deliver_invoice_items(
            invoice_id=invoice_id,
            items=items,
        )

        assert result is True
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        call_kwargs = mock_client.post.call_args[1]

        assert f"/v1/shops/{shop_id}/invoices/{invoice_id}/deliver" in call_url
        assert call_kwargs["json"] == {"items": items}
        assert call_kwargs["headers"]["Authorization"] == "Bearer test_api_key"


@pytest.mark.asyncio
async def test_store_client_auth_error_raised() -> None:
    """HTTP 401 or 403 on API request raises StoreAuthError and never silently swallows."""
    shop_id = 226299
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = json.dumps({"message": "Unauthenticated."})

    mock_client = MagicMock()
    mock_client.put = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        client = SellAuthStoreClient(api_key="broken_key", shop_id=shop_id)
        with pytest.raises(StoreAuthError):
            await client.update_stock(product_id=1, variant_id=2, stock=5)

