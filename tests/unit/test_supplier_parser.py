"""Unit tests for the supplier storefront HTML parser."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from resell.adapters.supplier_scrape import (
    SupplierParseError,
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
