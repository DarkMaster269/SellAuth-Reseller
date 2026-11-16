"""Contract tests ensuring every committed supplier HTML fixture parses successfully."""

from __future__ import annotations

from pathlib import Path

from resell.adapters.supplier_scrape import parse_supplier_html

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "supplier_pages"


def test_all_supplier_fixtures_parse() -> None:
    """Every .html fixture in supplier_pages directory must parse without error."""
    html_files = [f for f in FIXTURES_DIR.glob("*.html") if f.is_file() and f.stat().st_size > 0]
    assert len(html_files) >= 1, "At least one supplier page fixture must exist."

    for file_path in html_files:
        html = file_path.read_text(encoding="utf-8")
        variants = parse_supplier_html(html)
        assert len(variants) >= 1, f"No variants parsed from {file_path.name}"
        for var in variants:
            assert var.id is not None
            assert var.price >= 0
            assert var.stock is not None

