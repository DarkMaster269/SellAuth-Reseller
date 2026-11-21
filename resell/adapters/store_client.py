"""SellAuth Store API client for stock management and invoice delivery."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class StoreApiError(RuntimeError):
    """Raised when the SellAuth Store API returns an error."""


class StoreAuthError(StoreApiError):
    """Raised when SellAuth Store API credentials are invalid (401/403)."""


class SellAuthStoreClient:
    """HTTP client communicating with SellAuth Merchant Store API."""

    def __init__(
        self,
        api_key: str,
        shop_id: int,
        base_url: str = "https://api.sellauth.com",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.shop_id = shop_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        """Construct standard authorization and content headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "resell-sellauth/0.1.0",
        }

    def _check_status(self, resp: httpx.Response, endpoint: str) -> None:
        """Validate response status and surface authentication or API errors."""
        if resp.status_code in (401, 403):
            logger.error(
                "SellAuth API authentication failed for shop %s: %s",
                self.shop_id,
                resp.status_code,
            )
            raise StoreAuthError(
                f"SellAuth API authentication failed ({resp.status_code}) on {endpoint}: "
                f"{resp.text}"
            )
        if resp.status_code >= 400:
            logger.error(
                "SellAuth API error on %s: status=%s, body=%s",
                endpoint,
                resp.status_code,
                resp.text,
            )
            raise StoreApiError(
                f"SellAuth API error ({resp.status_code}) on {endpoint}: {resp.text}"
            )

    async def update_stock(
        self,
        product_id: int,
        variant_id: int,
        stock: int,
    ) -> bool:
        """Update storefront variant stock via PUT /v1/shops/{shopId}/products/.../stock/..."""
        endpoint = f"/v1/shops/{self.shop_id}/products/{product_id}/stock/{variant_id}"
        url = f"{self.base_url}{endpoint}"
        payload = {"stock": stock}

        logger.info(
            "Updating SellAuth stock: product=%s, variant=%s, stock=%s",
            product_id,
            variant_id,
            stock,
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.put(url, json=payload, headers=self._headers())
            self._check_status(resp, endpoint)
            return True

    async def deliver_invoice_items(
        self,
        invoice_id: int,
        items: list[str],
    ) -> bool:
        """Deliver digital items to customer via POST /v1/shops/{shopId}/invoices/.../deliver."""
        endpoint = f"/v1/shops/{self.shop_id}/invoices/{invoice_id}/deliver"
        url = f"{self.base_url}{endpoint}"
        payload = {"items": items}

        logger.info(
            "Delivering items for invoice=%s, count=%s",
            invoice_id,
            len(items),
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            self._check_status(resp, endpoint)
            return True

    async def deliver(self, order: Any, items: list[str]) -> bool:
        """Deliver items to customer via invoice delivery API (implements DeliveryAdapter)."""
        invoice_id = getattr(order, "invoice_id", order)
        return await self.deliver_invoice_items(invoice_id, items)

    async def notify_delayed(self, order: Any) -> None:
        """Send notification that payment confirmation is delayed (implements DeliveryAdapter)."""
        order_id = getattr(order, "id", order)
        logger.info("Order %s confirmation is delayed, waiting on blockchain...", order_id)

    async def get_product(self, product_id: int) -> dict[str, Any]:
        """Fetch full product details via GET /v1/shops/{shopId}/products/{productId}."""
        endpoint = f"/v1/shops/{self.shop_id}/products/{product_id}"
        url = f"{self.base_url}{endpoint}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._headers())
            self._check_status(resp, endpoint)
            data = resp.json()
            return data if isinstance(data, dict) else {}
