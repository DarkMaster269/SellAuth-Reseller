"""Late delivery adapter communicating with SellAuth Invoice Deliver API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import SecretStr

from resell.adapters.delivery_base import DeliveryAdapter
from resell.models import Order

logger = logging.getLogger(__name__)


class DeliveryError(RuntimeError):
    """Raised when customer delivery fails."""


class DeliveryAuthError(DeliveryError):
    """Raised when SellAuth API credentials fail (401/403)."""


class LateDeliveryAdapter(DeliveryAdapter):
    """Adapter for delivering digital items via SellAuth replace-delivered API.

    Endpoint: POST /v1/shops/{shopId}/invoices/{invoiceId}/replace-delivered
    Docs: https://sellauth.mintlify.site/api-reference/invoices/replace-delivered-items
    """

    def __init__(
        self,
        api_key: str | SecretStr,
        shop_id: int,
        base_url: str = "https://api.sellauth.com",
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.api_key = (
            api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        )
        self.shop_id = shop_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

    def _headers(self) -> dict[str, str]:
        """Construct standard authorization and content headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "resell-sellauth/0.1.0",
        }

    async def deliver(self, order: Order, items: list[str]) -> bool:
        """Deliver digital items to customer via SellAuth replace-delivered API."""
        if not items:
            order_id = getattr(order, "id", "unknown")
            logger.error("Cannot deliver empty items list for order=%s", order_id)
            raise DeliveryError(f"Cannot deliver empty items list for order {order_id}")

        invoice_id = getattr(order, "invoice_id", order)
        invoice_item_id = (
            getattr(order, "invoice_item_id", None)
            or getattr(order, "item_id", None)
        )
        order_id = getattr(order, "id", invoice_id)

        if invoice_item_id:
            endpoint = f"/v1/shops/{self.shop_id}/invoices/{invoice_id}/replace-delivered"
            payload: dict[str, Any] = {
                "invoice_item_id": invoice_item_id,
                "replacements": items,
            }
            log_desc = f"replace-delivered (item={invoice_item_id})"
        else:
            logger.warning(
                "Order %s has missing invoice_item_id; falling back to /deliver endpoint.",
                order_id,
            )
            endpoint = f"/v1/shops/{self.shop_id}/invoices/{invoice_id}/deliver"
            payload = {"items": items}
            log_desc = "deliver (fallback)"

        url = f"{self.base_url}{endpoint}"

        logger.info(
            "Delivering %d item(s) via %s for order=%s (invoice_id=%s)",
            len(items),
            log_desc,
            order_id,
            invoice_id,
        )
        print(
            f"[DELIVERY] Initiating {log_desc} for order={order_id} (invoice={invoice_id}): "
            f"{len(items)} items...",
            flush=True,
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=payload, headers=self._headers())
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                logger.warning(
                    "Network error delivering invoice %s (attempt %d/%d): %s",
                    invoice_id,
                    attempt,
                    self.max_retries,
                    exc,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay_seconds * attempt)
                continue

            if resp.status_code in (401, 403):
                logger.error(
                    "SellAuth API authentication failed on invoice %s: %s",
                    invoice_id,
                    resp.status_code,
                )
                raise DeliveryAuthError(
                    f"SellAuth API authentication failed ({resp.status_code}) on {endpoint}: "
                    f"{resp.text}"
                )

            if resp.status_code >= 500:
                last_error = DeliveryError(
                    f"SellAuth server error ({resp.status_code}) on invoice {invoice_id}: "
                    f"{resp.text}"
                )
                logger.warning(
                    "SellAuth 5xx server error on invoice %s (attempt %d/%d): status=%s",
                    invoice_id,
                    attempt,
                    self.max_retries,
                    resp.status_code,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay_seconds * attempt)
                continue

            if resp.status_code >= 400:
                logger.error(
                    "SellAuth client error on delivery for invoice %s: status=%s, body=%s",
                    invoice_id,
                    resp.status_code,
                    resp.text,
                )
                raise DeliveryError(
                    f"SellAuth delivery failed ({resp.status_code}) on invoice {invoice_id}: "
                    f"{resp.text}"
                )

            logger.info(
                "Successfully delivered items for order=%s to invoice=%s",
                order_id,
                invoice_id,
            )
            print(
                f"[DELIVERY] Deliver SUCCESS for order={order_id} (invoice={invoice_id})",
                flush=True,
            )
            return True

        raise DeliveryError(
            f"Failed to deliver items for invoice {invoice_id} after {self.max_retries} attempts: "
            f"{last_error}"
        )

    async def notify_delayed(self, order: Order) -> None:
        """Send notification that payment confirmation is delayed."""
        order_id = getattr(order, "id", order)
        invoice_id = getattr(order, "invoice_id", "unknown")
        logger.warning(
            "Order %s (invoice_id=%s) delayed: awaiting crypto confirmation on blockchain.",
            order_id,
            invoice_id,
        )
