"""FastAPI webhook endpoint for SellAuth invoice notifications."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from resell.config import MappingConfig, Settings
from resell.jobs import JobRunnerProtocol
from resell.models import CreateOrderInput
from resell.orders import OrdersRepo
from resell.states import OrderState

logger = logging.getLogger(__name__)

router = APIRouter()


def compute_hmac(secret: str, raw_body: bytes) -> str:
    """Compute HMAC-SHA256 hex digest for given raw bytes and secret."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_hmac(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    """Verify HMAC-SHA256 signature in constant time."""
    if not signature_header:
        return False

    sig = signature_header.strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1]

    expected = compute_hmac(secret, raw_body)
    return hmac.compare_digest(expected.lower(), sig.lower())


def parse_webhook_payload(raw_body: bytes) -> dict[str, Any]:
    """Parse JSON body bytes and extract domain fields per docs/findings.md."""
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Malformed JSON body: {exc}") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object payload.")

    invoice_id = data.get("invoice_id") or data.get("id")
    if not invoice_id:
        raise HTTPException(status_code=400, detail="Missing required 'invoice_id' or 'id'.")

    item = data.get("item")
    if not item or not isinstance(item, dict):
        raise HTTPException(status_code=400, detail="Missing or invalid 'item' in payload.")

    item_id = item.get("id")
    product_id = item.get("product_id")
    variant_id = item.get("variant_id")
    quantity = item.get("quantity", 1)

    if None in (item_id, product_id, variant_id):
        raise HTTPException(status_code=400, detail="Item missing required ID fields.")

    email = data.get("email")
    if not email and isinstance(data.get("customer"), dict):
        email = data["customer"].get("email")
    if not email:
        email = "unknown@buyer.com"

    price_str = (
        item.get("total_price_usd")
        or item.get("price")
        or data.get("price_usd")
        or "0.00"
    )
    try:
        price_paid_usd = Decimal(str(price_str))
    except (InvalidOperation, TypeError):
        price_paid_usd = Decimal("0.00")

    currency = data.get("currency") or "USD"

    return {
        "invoice_id": int(invoice_id),
        "item_id": int(item_id),
        "product_id": int(product_id),
        "variant_id": int(variant_id),
        "quantity": int(quantity),
        "email": str(email),
        "price_paid_usd": price_paid_usd,
        "currency": str(currency),
        "custom_fields": data.get("custom_fields") or item.get("custom_fields"),
    }


def create_webhook_handler(
    settings: Settings,
    repo: OrdersRepo,
    runner: JobRunnerProtocol,
    mapping: MappingConfig | None = None,
) -> APIRouter:
    """Create a configured webhook router with injected dependencies."""
    webhook_router = APIRouter()

    @webhook_router.post("/api/v1/deliver")
    @webhook_router.post("/deliver")
    async def handle_deliver_webhook(
        request: Request,
        x_signature: str | None = Header(default=None, alias="X-Signature"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Response:
        """Process incoming SellAuth webhook, verify signature, and enqueue order."""
        raw_body = await request.body()
        secret = settings.SELLAUTH_WEBHOOK_SECRET.get_secret_value()

        # 1. Verify HMAC signature on raw body bytes
        if not verify_hmac(secret, raw_body, x_signature):
            logger.warning("Rejected webhook request with invalid or missing HMAC signature.")
            raise HTTPException(status_code=401, detail="Invalid signature.")

        # 2. Parse payload using confirmed field names
        fields = parse_webhook_payload(raw_body)
        invoice_id = fields["invoice_id"]
        variant_id = fields["variant_id"]

        logger.info(
            "Accepted valid webhook for invoice_id=%d, variant_id=%d (idempotency=%s)",
            invoice_id,
            variant_id,
            idempotency_key,
        )

        # 3. Create order idempotently
        order_input = CreateOrderInput(
            invoice_id=invoice_id,
            item_id=fields["item_id"],
            product_id=fields["product_id"],
            variant_id=variant_id,
            quantity=fields["quantity"],
            email=fields["email"],
            price_paid_usd=fields["price_paid_usd"],
            currency=fields["currency"],
        )
        order, created = repo.create_order_if_new(order_input)

        # 4. Check mapping & submit job
        if created:
            variant_map = mapping.get_variant(variant_id) if mapping else None
            if mapping and (variant_map is None or not variant_map.enabled):
                # Fail-closed per SECURITY.md
                logger.error(
                    "Order %d has unmapped/disabled variant_id=%d. Setting refund_needed.",
                    order.id,
                    variant_id,
                )
                repo.transition(
                    order_id=order.id,
                    to_state=OrderState.REFUND_NEEDED,
                    reason=f"Unmapped variant_id={variant_id}",
                )
            else:
                runner.submit(order.id)
        else:
            logger.info(
                "Duplicate webhook delivery for invoice_id=%d; existing order returned.",
                invoice_id,
            )

        # 5. Fast 200 OK reply to satisfy SellAuth 10s timeout
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "delivered": "RECEIVED",
                "order_id": order.id,
                "state": order.state.value,
            },
        )

    return webhook_router
