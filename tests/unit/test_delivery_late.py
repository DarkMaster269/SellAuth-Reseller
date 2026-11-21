"""Unit tests for LateDeliveryAdapter communicating with SellAuth Invoice Deliver API."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from resell.adapters.delivery_late import DeliveryAuthError, DeliveryError, LateDeliveryAdapter
from resell.models import Order
from resell.states import OrderState


@pytest.fixture
def fake_order() -> Order:
    """Fixture providing a mock Order object with valid attributes."""
    now_iso = "2026-11-18T00:00:00+00:00"
    return Order(
        id=42,
        invoice_id=98765432,
        item_id=12345,
        invoice_item_id=12345,
        product_id=999,
        variant_id=888,
        quantity=1,
        email="buyer@example.com",
        price_paid_usd=Decimal("5.00"),
        currency="USD",
        state=OrderState.FETCHED,
        created_at=now_iso,
        updated_at=now_iso,
    )


@pytest.mark.asyncio
async def test_deliver_replace_delivered_success(fake_order: Order) -> None:
    """Successful delivery sends POST to replace-delivered with invoice_item_id and replacements."""
    shop_id = 123456
    items = ["CODE-KEY-12345", "CODE-KEY-67890"]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "delivered": 2}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = LateDeliveryAdapter(api_key="sk_live_fake_key", shop_id=shop_id)
        result = await adapter.deliver(fake_order, items)

        assert result is True
        mock_client.post.assert_called_once()
        url = mock_client.post.call_args[0][0]
        kwargs = mock_client.post.call_args[1]

        expected_endpoint = (
            f"/v1/shops/{shop_id}/invoices/{fake_order.invoice_id}/replace-delivered"
        )
        assert expected_endpoint in url
        assert kwargs["json"] == {
            "invoice_item_id": fake_order.invoice_item_id,
            "replacements": items,
        }
        assert kwargs["headers"]["Authorization"] == "Bearer sk_live_fake_key"


@pytest.mark.asyncio
async def test_deliver_fallback_when_invoice_item_id_missing(fake_order: Order) -> None:
    """When invoice_item_id is missing, falls back to /deliver with items array."""
    order_no_item_id = fake_order.model_copy(update={"invoice_item_id": None, "item_id": None})
    shop_id = 123456
    items = ["CODE-KEY-12345"]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = LateDeliveryAdapter(api_key="sk_live_fake_key", shop_id=shop_id)
        result = await adapter.deliver(order_no_item_id, items)

        assert result is True
        mock_client.post.assert_called_once()
        url = mock_client.post.call_args[0][0]
        kwargs = mock_client.post.call_args[1]

        expected_endpoint = f"/v1/shops/{shop_id}/invoices/{fake_order.invoice_id}/deliver"
        assert expected_endpoint in url
        assert kwargs["json"] == {"items": items}


@pytest.mark.asyncio
async def test_deliver_empty_items_raises_error(fake_order: Order) -> None:
    """Attempting to deliver an empty items list raises DeliveryError."""
    adapter = LateDeliveryAdapter(api_key="sk_live_fake_key", shop_id=123456)
    with pytest.raises(DeliveryError, match="Cannot deliver empty items"):
        await adapter.deliver(fake_order, [])


@pytest.mark.asyncio
async def test_deliver_auth_error_raises_delivery_auth_error(fake_order: Order) -> None:
    """HTTP 401 or 403 on delivery raises DeliveryAuthError without retrying."""
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = LateDeliveryAdapter(
            api_key="invalid_key",
            shop_id=123456,
            max_retries=3,
        )
        with pytest.raises(DeliveryAuthError, match="authentication failed"):
            await adapter.deliver(fake_order, ["ITEM-1"])

        # Must not retry on auth failures
        assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_deliver_client_error_raises_delivery_error(fake_order: Order) -> None:
    """HTTP 404 or 422 raises DeliveryError immediately without retrying."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = '{"message": "Invoice not found"}'

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = LateDeliveryAdapter(api_key="sk_live_fake_key", shop_id=123456)
        with pytest.raises(DeliveryError, match="SellAuth delivery failed"):
            await adapter.deliver(fake_order, ["ITEM-1"])

        assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_deliver_transient_5xx_retries_and_succeeds(fake_order: Order) -> None:
    """Transient HTTP 500 error retries and succeeds on next attempt."""
    resp_500 = MagicMock()
    resp_500.status_code = 500
    resp_500.text = "Internal Server Error"

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"success": True}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_500, resp_200])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = LateDeliveryAdapter(
            api_key="sk_live_fake_key",
            shop_id=123456,
            max_retries=3,
            retry_delay_seconds=0.01,
        )
        result = await adapter.deliver(fake_order, ["ITEM-1"])

        assert result is True
        assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_deliver_network_error_exhausts_retries_and_raises(fake_order: Order) -> None:
    """Persistent connection failures retry max_retries times and raise DeliveryError."""
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = LateDeliveryAdapter(
            api_key="sk_live_fake_key",
            shop_id=123456,
            max_retries=2,
            retry_delay_seconds=0.01,
        )
        with pytest.raises(DeliveryError, match="Failed to deliver items for invoice"):
            await adapter.deliver(fake_order, ["ITEM-1"])

        assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_notify_delayed_logs_without_raising(fake_order: Order) -> None:
    """notify_delayed logs delay without throwing exceptions."""
    adapter = LateDeliveryAdapter(api_key="sk_live_fake_key", shop_id=123456)
    # Must complete cleanly
    await adapter.notify_delayed(fake_order)
