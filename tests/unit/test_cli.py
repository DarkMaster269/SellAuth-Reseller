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
