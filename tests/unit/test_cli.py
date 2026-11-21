"""Unit tests for resell CLI commands."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from resell.cli import format_variants_table, parse_supplier_url
from resell.models import Variant


def test_parse_supplier_url_standard() -> None:
    """Parse standard https supplier product URL."""
    domain, slug = parse_supplier_url("https://vna.mysellauth.com/product/3m-nitro-promos")
    assert domain == "vna.mysellauth.com"
    assert slug == "3m-nitro-promos"


def test_parse_supplier_url_trailing_slash_and_no_scheme() -> None:
    """Parse URL without scheme and with trailing slash."""
    domain, slug = parse_supplier_url("vna.mysellauth.com/product/3m-nitro-promos/")
    assert domain == "vna.mysellauth.com"
    assert slug == "3m-nitro-promos"


def test_parse_supplier_url_invalid() -> None:
    """Malformed or empty URLs raise ValueError."""
    with pytest.raises(ValueError):
        parse_supplier_url("")


def test_format_variants_table() -> None:
    """Verify table formatting output contains variant details."""
    variants = [
        Variant(
            id="101",
            name="3 Months Nitro Token",
            price=Decimal("4.50"),
            stock=15,
            min_quantity=1,
            max_quantity=5,
        ),
        Variant(
            id="102",
            name="1 Month Nitro",
            price=Decimal("2.00"),
            stock=0,
            min_quantity=1,
            max_quantity=None,
        ),
    ]
    table = format_variants_table("vna.mysellauth.com", "3m-nitro-promos", variants)
    assert "101" in table
    assert "3 Months Nitro Token" in table
    assert "$4.50" in table
    assert "15" in table
    assert "1 / 5" in table
    assert "102" in table
    assert "1 / inf" in table


@pytest.mark.asyncio
async def test_inspect_supplier_command_success() -> None:
    """Mocked inspect call formats and returns cleanly."""
    fake_variants = [
        Variant(
            id="999",
            name="Test Variant",
            price=Decimal("5.00"),
            stock=10,
            min_quantity=1,
            max_quantity=None,
        )
    ]
    with patch(
        "resell.adapters.supplier_scrape.SupplierScraper.list_variants",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.return_value = fake_variants
        from resell.cli import run_inspect

        output = await run_inspect("https://vna.mysellauth.com/product/3m-nitro-promos")
        assert "Test Variant" in output
        assert "$5.00" in output


@pytest.mark.asyncio
async def test_run_sync_command() -> None:
    """Mocked run_sync executes and returns variant stock mapping."""
    with patch(
        "resell.stock_sync.StockSynchronizer.sync_all",
        new_callable=AsyncMock,
    ) as mock_sync:
        mock_sync.return_value = {2092559: 14}
        from resell.cli import run_sync

        results = await run_sync()
        assert results == {2092559: 14}


@pytest.mark.asyncio
async def test_run_pay_test_command() -> None:
    """Mocked run_pay_test queries balance and returns tuple."""
    with patch(
        "resell.adapters.payment_electrum.ElectrumPaymentAdapter.balance_sats",
        new_callable=AsyncMock,
    ) as mock_bal:
        mock_bal.return_value = 25000000
        from resell.cli import run_pay_test

        balance, txid = await run_pay_test()
        assert balance == 25000000
        assert txid is None


@pytest.mark.asyncio
async def test_run_buy_test_no_money(tmp_path: pytest.TempPathFactory) -> None:
    """run_buy_test creates order & checkout in DB and stops when ALLOW_MONEY_TESTS!=1."""
    import os

    from resell.cli import run_buy_test
    from resell.models import CheckoutRef

    fake_ref = CheckoutRef(
        supplier_domain="vna.mysellauth.com",
        supplier_checkout_id="test_inv_123",
        ltc_address="ltc1qfakeaddress",
        ltc_amount_sats=50000,
    )

    supplier_email = "supplier_orders@test.com"
    db_path = str(tmp_path / "test_buy.db")
    with (
        patch.dict(
            os.environ,
            {
                "ALLOW_MONEY_TESTS": "0",
                "SQLITE_PATH": db_path,
                "SUPPLIER_EMAIL": supplier_email,
            },
        ),
        patch(
            "resell.adapters.supplier_scrape.SupplierScraper.create_checkout",
            new_callable=AsyncMock,
        ) as mock_co,
        patch("resell.jobs.JobRunner.resume", new_callable=AsyncMock) as mock_resume,
    ):
        mock_co.return_value = fake_ref
        await run_buy_test()

        assert mock_co.called
        # Supplier checkout must use SUPPLIER_EMAIL to protect buyer privacy
        assert mock_co.call_args.kwargs["email"] == supplier_email
        assert not mock_resume.called

        # Test passing custom buyer email
        mock_co.reset_mock()
        await run_buy_test(email="realbuyer@gmail.com")
        assert mock_co.called
        # Supplier checkout still receives SUPPLIER_EMAIL
        assert mock_co.call_args.kwargs["email"] == supplier_email

        # Test selecting specific variant by store ID
        mock_co.reset_mock()
        await run_buy_test(variant_id=2092559)
        assert mock_co.called

        # Test selecting specific variant by supplier variant string ID
        mock_co.reset_mock()
        await run_buy_test(variant_id="variant_vna_nitro_3m")
        assert mock_co.called

        # Test invalid variant ID raises ValueError
        with pytest.raises(ValueError, match="not found in mapping.yaml"):
            await run_buy_test(variant_id="non_existent_variant_99999")


def test_handle_buy_test() -> None:
    """handle_buy_test calls run_buy_test and translates errors to status codes."""
    import argparse

    from resell.cli import handle_buy_test

    async def fake_success(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Checkout failed")

    with patch("resell.cli.run_buy_test", side_effect=fake_success):
        args = argparse.Namespace(variant_id=None, quantity=1, email="test@real.com")
        assert handle_buy_test(args) == 0

    with patch("resell.cli.run_buy_test", side_effect=fake_fail):
        args = argparse.Namespace(variant_id=None, quantity=1, email="test@real.com")
        assert handle_buy_test(args) == 1


@pytest.mark.asyncio
async def test_run_resume() -> None:
    """run_resume initializes runner and calls resume and process_all_pending."""
    from unittest.mock import MagicMock

    from resell.cli import run_resume
    from resell.models import Order

    fake_order = MagicMock(spec=Order, id=42)

    with (
        patch("resell.jobs.JobRunner.resume", new_callable=AsyncMock) as mock_resume,
        patch("resell.jobs.JobRunner.process_all_pending", new_callable=AsyncMock) as mock_proc,
    ):
        mock_resume.return_value = [fake_order]
        await run_resume()

        assert mock_resume.called
        assert mock_proc.called


def test_handle_resume() -> None:
    """handle_resume calls run_resume and translates errors to status codes."""
    import argparse

    from resell.cli import handle_resume

    async def fake_success() -> None:
        return None

    async def fake_fail() -> None:
        raise RuntimeError("Resume failed")

    with patch("resell.cli.run_resume", side_effect=fake_success):
        args = argparse.Namespace()
        assert handle_resume(args) == 0

    with patch("resell.cli.run_resume", side_effect=fake_fail):
        args = argparse.Namespace()
        assert handle_resume(args) == 1

