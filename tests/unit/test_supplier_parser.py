"""Unit tests for the supplier storefront HTML parser."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resell.adapters.supplier_scrape import (
    SupplierParseError,
    SupplierScraper,
    parse_supplier_html,
)

FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "supplier_pages" / "vna_3m_nitro.html"
)


def test_parse_real_supplier_fixture() -> None:
    """Parser must correctly extract product variants, prices, and stock from real fixture."""
    assert FIXTURE_PATH.is_file(), f"Fixture file not found: {FIXTURE_PATH}"
    html = FIXTURE_PATH.read_text(encoding="utf-8")

    variants = parse_supplier_html(html)
    assert len(variants) == 1

    variant = variants[0]
    assert variant.id == "1087829"
    assert variant.name == "Default"
    assert variant.price == Decimal("0.40")
    assert variant.stock == 27
    assert variant.min_quantity == 1
    assert variant.max_quantity is None


def test_parse_missing_product_raises_error() -> None:
    """HTML without product data must raise SupplierParseError."""
    html_without_product = "<html><body><h1>Empty Store</h1></body></html>"
    with pytest.raises(SupplierParseError):
        parse_supplier_html(html_without_product)


@pytest.mark.asyncio
async def test_supplier_scraper_503_pow_retry() -> None:
    """Scraper intercepts 503 challenge, solves PoW, sets cookie, and retries request."""
    challenge_html = "<html>503 Challenge Page</html>"
    success_html = FIXTURE_PATH.read_text(encoding="utf-8")

    resp_503 = MagicMock()
    resp_503.status_code = 503
    resp_503.text = challenge_html

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.text = success_html

    mock_session = MagicMock()
    mock_session.get = AsyncMock(side_effect=[resp_503, resp_200])
    mock_session.cookies = MagicMock()

    with (
        patch("resell.adapters.supplier_scrape.AsyncSession") as mock_session_cls,
        patch(
            "resell.adapters.supplier_scrape.solve_sellauth_503",
            return_value="yX3=6C8210CEF92BD6ADB225A59FC2647DC78E6813A50",
        ),
    ):
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        scraper = SupplierScraper()
        variants = await scraper.list_variants("vna.mysellauth.com", "3m-nitro-promos")

        assert len(variants) == 1
        assert variants[0].id == "1087829"
        assert mock_session.get.call_count == 2
        # Check that dynamic cookie yX3 was passed
        mock_session.cookies.set.assert_called_once_with(
            "yX3", "6C8210CEF92BD6ADB225A59FC2647DC78E6813A50"
        )
        _, second_kwargs = mock_session.get.call_args_list[1]
        assert second_kwargs.get("cookies") == {
            "yX3": "6C8210CEF92BD6ADB225A59FC2647DC78E6813A50"
        }

