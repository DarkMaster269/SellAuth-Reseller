"""Unit tests for Discord alerts system verifying pure ASCII, routing, and embeds."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from resell.alerts import DiscordAlerts, mask_item, sanitize_ascii
from resell.config import Settings
from resell.models import Order, Purchase
from resell.states import OrderState


def is_pure_ascii(text: str) -> bool:
    """Helper verifying text contains strictly ASCII printable characters."""
    return all(ord(c) <= 126 and (ord(c) >= 32 or c in "\n\t\r") for c in text)


def assert_embed_ascii(payload: dict[str, Any]) -> None:
    """Recursively verify all string values in Discord payload are pure ASCII."""
    if "content" in payload and payload["content"]:
        assert is_pure_ascii(payload["content"])
    for embed in payload.get("embeds", []):
        if "title" in embed:
            assert is_pure_ascii(embed["title"])
        if "description" in embed:
            assert is_pure_ascii(embed["description"])
        for field in embed.get("fields", []):
            assert is_pure_ascii(field["name"])
            assert is_pure_ascii(field["value"])


@pytest.fixture
def mock_http() -> AsyncMock:
    """Mock HTTP client recording sent JSON payloads."""
    client = AsyncMock()
    resp = AsyncMock()
    resp.status_code = 204
    client.post.return_value = resp
    return client


@pytest.fixture
def test_settings() -> Settings:
    """Settings configured with dedicated Discord webhook URLs."""
    return Settings(
        SELLAUTH_API_KEY="sk_test_fake",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET="sec",
        DISCORD_WEBHOOK_ORDERS="https://discord.com/api/webhooks/orders/123",
        DISCORD_WEBHOOK_SUPPLIER="https://discord.com/api/webhooks/supplier/456",
        DISCORD_WEBHOOK_DELIVERED="https://discord.com/api/webhooks/delivered/789",
        DISCORD_WEBHOOK_ALERTS="https://discord.com/api/webhooks/alerts/999",
        DISCORD_ADMIN_ROLE_ID="999888777",
    )


@pytest.mark.asyncio
async def test_order_received_routes_to_orders_webhook_and_is_ascii(
    test_settings: Settings,
    mock_http: AsyncMock,
) -> None:
    """send_order_received posts to DISCORD_WEBHOOK_ORDERS with strictly ASCII embed."""
    alerts = DiscordAlerts(test_settings, http_client=mock_http)
    order = Order(
        id=101,
        invoice_id=202401,
        item_id=1,
        product_id=10,
        variant_id=20,
        quantity=2,
        email="buyer@test.com",
        price_paid_usd=Decimal("12.50"),
        currency="USD",
        state=OrderState.RECEIVED,
        created_at="2026-11-22T00:00:00Z",
        updated_at="2026-11-22T00:00:00Z",
    )

    success = await alerts.send_order_received(order, variant_name="3M Nitro Boost")
    assert success is True
    assert mock_http.post.called

    url, kwargs = mock_http.post.call_args[0][0], mock_http.post.call_args[1]
    assert url == "https://discord.com/api/webhooks/orders/123"

    payload = kwargs["json"]
    assert_embed_ascii(payload)
    embed = payload["embeds"][0]
    assert embed["title"] == "[ORDER RECEIVED]"
    assert any(f["name"] == "Invoice ID" and f["value"] == "#202401" for f in embed["fields"])
    assert any(f["name"] == "Price Paid" and "$12.50 USD" in f["value"] for f in embed["fields"])


@pytest.mark.asyncio
async def test_supplier_purchase_routes_to_supplier_webhook_and_is_ascii(
    test_settings: Settings,
    mock_http: AsyncMock,
) -> None:
    """send_supplier_purchase posts to DISCORD_WEBHOOK_SUPPLIER with ASCII embed."""
    alerts = DiscordAlerts(test_settings, http_client=mock_http)
    order = Order(
        id=102,
        invoice_id=202402,
        item_id=1,
        product_id=10,
        variant_id=20,
        quantity=1,
        email="buyer@test.com",
        price_paid_usd=Decimal("6.00"),
        currency="USD",
        state=OrderState.CHECKOUT_CREATED,
        created_at="2026-11-22T00:00:00Z",
        updated_at="2026-11-22T00:00:00Z",
    )
    purchase = Purchase(
        id=1,
        order_id=102,
        supplier_domain="vna.mysellauth.com",
        supplier_product_slug="3m-nitro",
        supplier_variant_id="var_1",
        supplier_checkout_id="chk_sup_999",
        ltc_address="ltc1qfakecryptoaddress123",
        ltc_amount_sats=1250000,
        txid="abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        created_at="2026-11-22T00:00:00Z",
        updated_at="2026-11-22T00:00:00Z",
    )

    success = await alerts.send_supplier_purchase(order, purchase)
    assert success is True

    url, kwargs = mock_http.post.call_args[0][0], mock_http.post.call_args[1]
    assert url == "https://discord.com/api/webhooks/supplier/456"

    payload = kwargs["json"]
    assert_embed_ascii(payload)
    embed = payload["embeds"][0]
    assert embed["title"] == "[SUPPLIER PURCHASE]"
    assert any("vna.mysellauth.com" in f["value"] for f in embed["fields"])
    assert any("1250000 sats" in f["value"] for f in embed["fields"])


@pytest.mark.asyncio
async def test_order_delivered_routes_to_delivered_webhook_and_is_ascii(
    test_settings: Settings,
    mock_http: AsyncMock,
) -> None:
    """send_order_delivered posts to DISCORD_WEBHOOK_DELIVERED with profit & masked items."""
    alerts = DiscordAlerts(test_settings, http_client=mock_http)
    order = Order(
        id=103,
        invoice_id=202403,
        item_id=1,
        product_id=10,
        variant_id=20,
        quantity=1,
        email="buyer@test.com",
        price_paid_usd=Decimal("8.50"),
        currency="USD",
        state=OrderState.DELIVERED,
        created_at="2026-11-22T00:00:00Z",
        updated_at="2026-11-22T00:00:00Z",
    )
    items = ["secret_nitro_token_1234567890", "another_code_abcdef"]

    success = await alerts.send_order_delivered(
        order=order,
        items=items,
        elapsed_seconds=14.2,
        profit_usd=Decimal("3.50"),
        supplier_cost_usd=Decimal("5.00"),
    )
    assert success is True

    url, kwargs = mock_http.post.call_args[0][0], mock_http.post.call_args[1]
    assert url == "https://discord.com/api/webhooks/delivered/789"

    payload = kwargs["json"]
    assert_embed_ascii(payload)
    embed = payload["embeds"][0]
    assert embed["title"] == "[ORDER DELIVERED]"
    assert any(f["name"] == "Net Profit" and "$3.50" in f["value"] for f in embed["fields"])

    delivered_field = next(f for f in embed["fields"] if f["name"] == "Delivered Items")
    # Verify items are masked (neither token is shown in full plain-text)
    assert "secret_nitro_token_1234567890" not in delivered_field["value"]
    assert "..." in delivered_field["value"]


@pytest.mark.asyncio
async def test_critical_alert_pings_admin_role_and_warning_does_not(
    test_settings: Settings,
    mock_http: AsyncMock,
) -> None:
    """Critical alerts include <@&ROLE_ID> ping; normal alerts do not."""
    alerts = DiscordAlerts(test_settings, http_client=mock_http)

    # 1. Non-critical alert
    await alerts.send_alert(
        title="High Latency",
        message="Supplier response was 12s",
        is_critical=False,
    )
    kwargs1 = mock_http.post.call_args[1]["json"]
    assert_embed_ascii(kwargs1)
    assert "content" not in kwargs1
    assert kwargs1["embeds"][0]["title"] == "[ALERT] High Latency"

    # 2. Critical alert
    mock_http.reset_mock()
    await alerts.send_alert(
        title="Guard Breach",
        message="Daily spend limit exceeded",
        is_critical=True,
    )
    kwargs2 = mock_http.post.call_args[1]["json"]
    assert_embed_ascii(kwargs2)
    assert kwargs2["content"] == "<@&999888777>"
    assert kwargs2["embeds"][0]["title"] == "[CRITICAL] Guard Breach"


@pytest.mark.asyncio
async def test_low_balance_alert_rate_limiting_12_hours(
    test_settings: Settings,
    mock_http: AsyncMock,
) -> None:
    """Low wallet balance alert sends once and suppresses duplicates within 12 hours."""
    alerts = DiscordAlerts(test_settings, http_client=mock_http)

    # First call at t=1000 sends alert
    t1 = 1000.0
    sent1 = await alerts.send_low_balance_alert(5000000, 10000000, now_ts=t1)
    assert sent1 is True
    assert mock_http.post.called

    # Second call 1 hour later (t=4600) is suppressed
    mock_http.reset_mock()
    sent2 = await alerts.send_low_balance_alert(4000000, 10000000, now_ts=t1 + 3600)
    assert sent2 is False
    assert not mock_http.post.called

    # Third call after 12.5 hours (t=46000) sends alert again
    sent3 = await alerts.send_low_balance_alert(3000000, 10000000, now_ts=t1 + (12.5 * 3600))
    assert sent3 is True
    assert mock_http.post.called


def test_mask_item_helper() -> None:
    """mask_item correctly truncates tokens for security."""
    assert mask_item("short") == "sh..."
    assert mask_item("a_very_long_secret_key_value") == "a_ve...alue"
    assert sanitize_ascii("Clean ascii message") == "Clean ascii message"
    # Emojis/Unicode stripped cleanly
    assert sanitize_ascii("Alert 🔥 rocket 🚀 test") == "Alert rocket test"
