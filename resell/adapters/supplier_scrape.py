"""Supplier scraper using browser impersonation and SellAuth storefront parsing."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

from curl_cffi.requests import AsyncSession

from resell.models import CheckoutRef, Variant

logger = logging.getLogger(__name__)


class SupplierParseError(ValueError):
    """Raised when supplier storefront HTML cannot be parsed."""


class SupplierUnavailableError(RuntimeError):
    """Raised when supplier store returns anti-bot challenge or 503."""


def parse_supplier_html(html: str) -> list[Variant]:
    """Parse SellAuth storefront HTML and extract product variants."""
    # 1. Anti-bot check
    html_lower = html.lower()
    if "just a moment..." in html_lower or "503 service temporarily unavailable" in html_lower:
        if "product:" not in html:
            raise SupplierUnavailableError("Supplier page blocked by 503 anti-bot screen.")

    # 2. Extract Alpine.js productForm object (standard in SellAuth themes)
    product_dict = None
    match = re.search(
        r"product\s*:\s*(\{.*?\})\s*,\s*(?:productAddons|activeVariant)",
        html,
        re.DOTALL,
    )
    if match:
        try:
            product_dict = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.debug("Failed to decode Alpine.js product JSON: %s", exc)

    # 3. Fallback: Search for ld+json schema
    if not product_dict:
        ld_match = re.search(
            r'<script\s+type="application/ld\+json">\s*(\[.*?\]|\{.*?\})\s*</script>',
            html,
            re.DOTALL,
        )
        if ld_match:
            try:
                ld_data = json.loads(ld_match.group(1))
                items = ld_data if isinstance(ld_data, list) else [ld_data]
                for item in items:
                    if item.get("@type") == "ProductGroup" and "hasVariant" in item:
                        variants = []
                        for v in item["hasVariant"]:
                            sku = str(v.get("sku", ""))
                            # SKU format often: slug-variantId
                            v_id = sku.split("-")[-1] if "-" in sku else sku
                            offers = v.get("offers", {})
                            price_val = Decimal(str(offers.get("price", "0.00")))
                            variants.append(
                                Variant(
                                    id=v_id,
                                    name=v.get("name", "Default"),
                                    price=price_val,
                                    stock=1,
                                    min_quantity=1,
                                    max_quantity=None,
                                )
                            )
                        if variants:
                            return variants
            except Exception as exc:
                logger.debug("Failed to parse ld+json schema: %s", exc)

    if not product_dict or "variants" not in product_dict:
        raise SupplierParseError("Could not locate product variants in supplier HTML.")

    raw_variants = product_dict.get("variants", [])
    if not isinstance(raw_variants, list) or not raw_variants:
        raise SupplierParseError("Product variants list is empty or invalid.")

    parsed: list[Variant] = []
    for var in raw_variants:
        if not isinstance(var, dict):
            continue
        v_id = str(var.get("id"))
        v_name = str(var.get("name") or "Default")
        v_price = Decimal(str(var.get("price") or "0.00"))

        raw_stock = var.get("stock")
        stock = int(raw_stock) if raw_stock is not None else 0

        min_qty = int(var.get("quantity_min") or 1)
        max_qty = int(var["quantity_max"]) if var.get("quantity_max") else None

        parsed.append(
            Variant(
                id=v_id,
                name=v_name,
                price=v_price,
                stock=stock,
                min_quantity=min_qty,
                max_quantity=max_qty,
            )
        )

    if not parsed:
        raise SupplierParseError("No valid variants extracted from product.")

    return parsed


class SupplierScraper:
    """Supplier adapter implementation fetching storefront pages via curl_cffi."""

    def __init__(self, impersonate: str = "chrome120") -> None:
        self.impersonate = impersonate

    async def list_variants(self, domain: str, product_slug: str) -> list[Variant]:
        """Fetch supplier product page and parse live variants."""
        clean_domain = domain.replace("https://", "").replace("http://", "").strip("/")
        url = f"https://{clean_domain}/product/{product_slug}"
        logger.info("Scraping supplier product page: %s", url)

        async with AsyncSession(impersonate=self.impersonate) as session:
            resp = await session.get(
                url,
                timeout=15,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )

        if resp.status_code == 503:
            logger.warning("Supplier returned 503 anti-bot screen at %s", url)
            raise SupplierUnavailableError(f"Supplier returned 503 at {url}")

        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch {url}: HTTP {resp.status_code}")

        return parse_supplier_html(resp.text)

    async def create_checkout(
        self,
        domain: str,
        product_slug: str,
        variant_id: str,
        quantity: int,
    ) -> CheckoutRef:
        """Scheduled for Stage 8."""
        raise NotImplementedError("Real purchase create_checkout is scheduled for Stage 8.")

    async def lock_payment(
        self,
        domain: str,
        checkout_id: str,
        ltc_address: str,
    ) -> None:
        """Scheduled for Stage 8."""
        raise NotImplementedError("Real purchase lock_payment is scheduled for Stage 8.")

    async def get_status(self, domain: str, checkout_id: str) -> str:
        """Scheduled for Stage 8."""
        raise NotImplementedError("Real purchase get_status is scheduled for Stage 8.")

    async def fetch_items(self, domain: str, checkout_id: str) -> list[str]:
        """Scheduled for Stage 8."""
        raise NotImplementedError("Real purchase fetch_items is scheduled for Stage 8.")
