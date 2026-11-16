"""Live integration test against real supplier storefront (read-only, opt-in)."""

from __future__ import annotations

import pytest

from resell.adapters.supplier_scrape import SupplierScraper


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_supplier_read_only() -> None:
    """Fetch live supplier page and verify variants parse without error."""
    scraper = SupplierScraper()
    variants = await scraper.list_variants(
        domain="vna.mysellauth.com",
        product_slug="3m-nitro-promos",
    )
    assert len(variants) >= 1
    for v in variants:
        assert v.id is not None
        assert v.price >= 0
        assert v.stock is not None

