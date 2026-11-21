"""Unit tests for SupplierScraper checkout, status, and item retrieval methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resell.adapters.supplier_scrape import SupplierScraper, parse_supplier_html
from resell.models import CheckoutRef


@pytest.mark.asyncio
async def test_create_checkout_mocked() -> None:
    """create_checkout fetches page, solves Altcha, posts checkout, and returns CheckoutRef."""
    html_page = """
    <html>
    <body>
    <script>
        const product = {"id": 685714, "variants": [{"id": 1087829, "stock": 10, "price": "0.40"}]};
        const shopId = '169044';
    </script>
    <altcha-widget
        challengejson='{"algorithm":"SHA-256","challenge":"101b263f","maxnumber":100,"salt":"salt123","signature":"sig"}'
    ></altcha-widget>
    </body>
    </html>
    """

    resp_page = MagicMock()
    resp_page.status_code = 200
    resp_page.text = html_page

    resp_checkout = MagicMock()
    resp_checkout.status_code = 200
    resp_checkout.json.return_value = {
        "url": "https://vna.mysellauth.com/invoice/inv_test_12345",
        "invoice": {"id": 12345},
    }

    resp_payment = MagicMock()
    resp_payment.status_code = 200
    resp_payment.json.return_value = {
        "crypto_address": "ltc1qfakeaddress9999",
        "crypto_amount": "0.00500000",
        "gateway": "LTC",
    }

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=resp_page)
    mock_session.post = AsyncMock(side_effect=[resp_checkout, resp_payment])

    with (
        patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls,
        patch(
            "resell.adapters.supplier_scrape.solve_altcha",
            return_value={"base64_payload": "fake_altcha_payload_b64"},
        ),
    ):
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        ref = await scraper.create_checkout(
            domain="vna.mysellauth.com",
            product_slug="3m-nitro-promos",
            variant_id="1087829",
            quantity=1,
            email="buyer@example.com",
        )

        assert isinstance(ref, CheckoutRef)
        assert ref.supplier_domain == "vna.mysellauth.com"
        assert ref.supplier_checkout_id == "inv_test_12345"
        assert ref.ltc_address == "ltc1qfakeaddress9999"
        # 0.005 LTC = 500,000 Satoshis
        assert ref.ltc_amount_sats == 500000

        # Verify checkout POST payload included email and altcha tokens
        first_post_call = mock_session.post.call_args_list[0]
        assert first_post_call.kwargs["json"]["email"] == "buyer@example.com"
        assert first_post_call.kwargs["json"]["altcha"] == "fake_altcha_payload_b64"
        assert first_post_call.kwargs["json"]["verification_token"] == "fake_altcha_payload_b64"


@pytest.mark.asyncio
async def test_create_checkout_with_real_altcha_solving() -> None:
    """End-to-end Altcha solving extracts challenge from HTML and attaches solved token."""
    html_page = """
    <html>
    <body>
    <script>
        const product = {"id": 685714, "variants": [{"id": 1087829, "stock": 10, "price": "0.40"}]};
        const shopId = '169044';
    </script>
    <altcha-widget
        challengejson='{"algorithm":"SHA-256","challenge":"101b263f5d972164d0f0f46b45d331bc296cc65fe7ca2db695a1cb1887d63fdc","maxnumber":10000,"salt":"af87dc776958dad40f7d069d?expires=1794850521&","signature":"d6506dab1774eae1a8071b54d0ddca7e2d9806d0bb53ade5b8446c7111cd48c4"}'
    ></altcha-widget>
    </body>
    </html>
    """
    resp_page = MagicMock(status_code=200, text=html_page)
    resp_checkout = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={
                "url": "https://vna.mysellauth.com/invoice/inv_real_1",
                "invoice": {"id": 1},
            }
        ),
    )
    resp_payment = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={"crypto_address": "ltc1qfake", "crypto_amount": "0.005", "gateway": "LTC"}
        ),
    )

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=resp_page)
    mock_session.post = AsyncMock(side_effect=[resp_checkout, resp_payment])

    with patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls:
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        ref = await scraper.create_checkout(
            domain="vna.mysellauth.com",
            product_slug="3m-nitro-promos",
            variant_id="1087829",
            quantity=1,
            email="test@gmail.com",
        )

        assert isinstance(ref, CheckoutRef)
        post_json = mock_session.post.call_args_list[0].kwargs["json"]
        assert "altcha" in post_json
        assert "verification_token" in post_json
        assert post_json["altcha"] == post_json["verification_token"]
        assert len(post_json["altcha"]) > 20


@pytest.mark.asyncio
async def test_create_checkout_without_altcha() -> None:
    """Checkout succeeds cleanly when page does not contain an Altcha widget."""
    html_page = """
    <html>
    <body>
    <script>
        const product = {"id": 685714, "variants": [{"id": 1087829, "stock": 10, "price": "0.40"}]};
        const shopId = '169044';
    </script>
    </body>
    </html>
    """
    resp_page = MagicMock(status_code=200, text=html_page)
    resp_checkout = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={
                "url": "https://vna.mysellauth.com/invoice/inv_no_altcha",
                "invoice": {"id": 2},
            }
        ),
    )
    resp_payment = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={
                "crypto_address": "ltc1qfake",
                "crypto_amount": "0.005",
                "gateway": "LTC",
            }
        ),
    )

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=resp_page)
    mock_session.post = AsyncMock(side_effect=[resp_checkout, resp_payment])

    with patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls:
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        ref = await scraper.create_checkout(
            domain="vna.mysellauth.com",
            product_slug="3m-nitro-promos",
            variant_id="1087829",
            quantity=1,
        )

        assert isinstance(ref, CheckoutRef)
        post_json = mock_session.post.call_args_list[0].kwargs["json"]
        assert "altcha" not in post_json
        assert "verification_token" not in post_json


@pytest.mark.asyncio
async def test_get_status_completed() -> None:
    """get_status queries invoice and returns normalized status."""
    resp_status = MagicMock()
    resp_status.status_code = 200
    resp_status.json.return_value = {
        "status": "completed",
    }

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=resp_status)

    with patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls:
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        status = await scraper.get_status("vna.mysellauth.com", "inv_test_12345")
        assert status == "completed"


@pytest.mark.asyncio
async def test_fetch_items_success() -> None:
    """fetch_items retrieves delivered keys from completed invoice."""
    resp_invoice = MagicMock()
    resp_invoice.status_code = 200
    resp_invoice.json.return_value = {
        "status": "completed",
        "delivered": ["NITRO-TOKEN-AAA-BBB", "NITRO-TOKEN-CCC-DDD"],
    }

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=resp_invoice)

    with patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls:
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        items = await scraper.fetch_items("vna.mysellauth.com", "inv_test_12345")
        assert items == ["NITRO-TOKEN-AAA-BBB", "NITRO-TOKEN-CCC-DDD"]


def test_parse_supplier_html_trims_variant_id() -> None:
    """parse_supplier_html trims leading and trailing whitespace from variant IDs."""
    html = """
    <div x-data='{
        product: {
            "variants": [
                {"id": "2069452  ", "name": "Variant Trimming", "price": "0.05", "stock": 5}
            ]
        },
        activeVariant: null
    }'></div>
    """
    variants = parse_supplier_html(html)
    assert len(variants) == 1
    assert variants[0].id == "2069452"


@pytest.mark.asyncio
async def test_create_checkout_dynamic_altcha_modern_flow() -> None:
    """create_checkout handles modern SellAuth SPA: dynamic Altcha, variant trimming, and PUT."""
    html_page = """
    <html>
    <body>
    <script>
        const product = {
            "id": 992462,
            "variants": [{"id": "2069452  ", "stock": 17, "price": "0.05"}]
        };
        const shopId = '281368';
    </script>
    </body>
    </html>
    """
    resp_page = MagicMock(status_code=200, text=html_page)
    altcha_challenge = (
        '{"algorithm":"SHA-256","challenge":"c123","maxnumber":100,"salt":"s123","signature":"sig"}'
    )
    resp_altcha = MagicMock(status_code=200, text=altcha_challenge)

    resp_checkout = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"url": "https://dnclub.shop/checkout/chk_uuid_999"}),
    )
    resp_full_1 = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={
                "shop": {
                    "payment_methods": [
                        {"id": 241907, "name": "Litecoin", "type": "LTC"},
                        {"id": 111111, "name": "Bitcoin", "type": "BTC"},
                    ]
                }
            }
        ),
    )
    resp_put = MagicMock(status_code=200, json=MagicMock(return_value={"success": True}))
    resp_full_2 = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={
                "invoice": {
                    "crypto_address": "ltc1qdnclubrealaddress123",
                    "crypto_amount": "0.00075000",
                }
            }
        ),
    )

    mock_session = MagicMock()
    mock_session.get = AsyncMock(
        side_effect=[resp_page, resp_altcha, resp_full_1, resp_altcha, resp_full_2]
    )
    mock_session.post = AsyncMock(return_value=resp_checkout)
    mock_session.put = AsyncMock(return_value=resp_put)

    with (
        patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls,
        patch(
            "resell.adapters.supplier_scrape.solve_altcha",
            return_value={"base64_payload": "fake_solved_altcha_token", "number": 42},
        ),
    ):
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        ref = await scraper.create_checkout(
            domain="dnclub.shop",
            product_slug="fresh-ev-fa-custom-domain",
            variant_id="2069452  ",  # Trailing whitespace
            quantity=1,
            email="realbuyer@gmail.com",
        )

        assert isinstance(ref, CheckoutRef)
        assert ref.supplier_domain == "dnclub.shop"
        assert ref.supplier_checkout_id == "chk_uuid_999"
        assert ref.ltc_address == "ltc1qdnclubrealaddress123"
        assert ref.ltc_amount_sats == 75000

        # Verify POST payload sent integer variantId and stripped spaces
        post_kwargs = mock_session.post.call_args.kwargs
        cart_item = post_kwargs["json"]["cart"][0]
        assert cart_item["variantId"] == 2069452
        assert post_kwargs["json"]["email"] == "realbuyer@gmail.com"
        assert post_kwargs["json"]["altcha"] == "fake_solved_altcha_token"

        # Verify PUT was called with payment_method_id and altcha
        put_kwargs = mock_session.put.call_args.kwargs
        assert put_kwargs["json"]["payment_method_id"] == 241907
        assert put_kwargs["json"]["altcha"] == "fake_solved_altcha_token"


@pytest.mark.asyncio
async def test_get_status_modern_checkout() -> None:
    """get_status falls back to /checkout/{id}/full when /invoices/{id} returns 404."""
    resp_invoice = MagicMock(status_code=404)
    resp_checkout = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"invoice": {"status": "completed"}}),
    )

    mock_session = MagicMock()
    mock_session.get = AsyncMock(side_effect=[resp_invoice, resp_checkout])

    with patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls:
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        status = await scraper.get_status("dnclub.shop", "chk_uuid_123")
        assert status == "completed"


@pytest.mark.asyncio
async def test_fetch_items_modern_checkout() -> None:
    """fetch_items falls back to /checkout/{id}/full when /invoices/{id} returns 404."""
    resp_invoice = MagicMock(status_code=404)
    resp_checkout = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"invoice": {"delivered": ["KEY-1", "KEY-2"]}}),
    )

    mock_session = MagicMock()
    mock_session.get = AsyncMock(side_effect=[resp_invoice, resp_checkout])

    with patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls:
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        items = await scraper.fetch_items("dnclub.shop", "chk_uuid_123")
        assert items == ["KEY-1", "KEY-2"]


@pytest.mark.asyncio
async def test_create_checkout_extracts_correct_product_id_not_category() -> None:
    """create_checkout extracts product.id and does not greedily grab category.id."""
    html_page = """
    <html><body><script>
    Alpine.data('productForm', () => ({
        product: {
            "id": 992462,
            "name": "Test Item",
            "category_id": 32295,
            "category": {"id": 32295, "name": "Accounts"},
            "variants": [{"id": 2069452, "stock": 10, "price": "0.03"}]
        },
        activeVariant: null
    }));
    const shopId = '281368';
    </script>
    <altcha-widget
        challengejson='{"algorithm":"SHA-256","challenge":"c","maxnumber":10,"salt":"s","signature":"sig"}'
    ></altcha-widget>
    </body></html>
    """
    resp_page = MagicMock(status_code=200, text=html_page)
    resp_checkout = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"url": "https://dnclub.shop/invoice/123"}),
    )
    resp_payment = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"crypto_address": "ltc1qfake", "crypto_amount": "0.001"}),
    )

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=resp_page)
    mock_session.post = AsyncMock(side_effect=[resp_checkout, resp_payment])

    with (
        patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls,
        patch(
            "resell.adapters.supplier_scrape.solve_altcha",
            return_value={"base64_payload": "token", "number": 1},
        ),
    ):
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        ref = await scraper.create_checkout(
            domain="dnclub.shop",
            product_slug="test-slug",
            variant_id="2069452",
            quantity=1,
        )

        assert isinstance(ref, CheckoutRef)
        post_kwargs = mock_session.post.call_args_list[0].kwargs
        cart = post_kwargs["json"]["cart"]
        assert len(cart) == 1
        assert cart[0]["productId"] == 992462
        assert isinstance(cart[0]["productId"], int)
        assert cart[0]["productId"] != 32295
