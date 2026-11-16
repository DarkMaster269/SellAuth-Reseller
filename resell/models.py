"""Shared Pydantic domain models for resell-sellauth."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from resell.states import OrderState


class CreateOrderInput(BaseModel):
    """Input parameters to create a new order."""

    invoice_id: int
    item_id: int
    product_id: int
    variant_id: int
    quantity: int = 1
    email: str
    price_paid_usd: Decimal = Field(default=Decimal("0.00"))
    currency: str = "USD"


class Order(BaseModel):
    """Domain model representing a customer order."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    item_id: int
    product_id: int
    variant_id: int
    quantity: int
    email: str
    price_paid_usd: Decimal
    currency: str
    state: OrderState
    created_at: str
    updated_at: str


class CreatePurchaseInput(BaseModel):
    """Input parameters to create a new supplier purchase."""

    order_id: int
    supplier_domain: str
    supplier_product_slug: str
    supplier_variant_id: str
    supplier_checkout_id: str | None = None
    ltc_address: str | None = None
    ltc_amount_sats: int | None = None
    txid: str | None = None
    items_delivered: list[str] | None = None


class Purchase(BaseModel):
    """Domain model representing a purchase from a supplier."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    supplier_domain: str
    supplier_product_slug: str
    supplier_variant_id: str
    supplier_checkout_id: str | None = None
    ltc_address: str | None = None
    ltc_amount_sats: int | None = None
    txid: str | None = None
    items_delivered: list[str] | None = None
    created_at: str
    updated_at: str


class OrderEvent(BaseModel):
    """Domain model representing an audit log entry for order transitions."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    from_state: OrderState | None
    to_state: OrderState
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str


class CheckoutRef(BaseModel):
    """Reference returned when creating checkout at a supplier."""

    supplier_domain: str
    supplier_checkout_id: str
    ltc_address: str
    ltc_amount_sats: int
    expires_at: str | None = None


class PaymentRequest(BaseModel):
    """Payment instructions for Electrum-LTC."""

    ltc_address: str
    amount_sats: int
    order_id: int


class Variant(BaseModel):
    """Supplier product variant."""

    id: str | int
    name: str
    price: Decimal
    stock: int
    min_quantity: int = 1
    max_quantity: int | None = None


class WebhookPayload(BaseModel):
    """Model matching SellAuth dynamic delivery webhook payload."""

    event: str
    delivery_reason: str | None = None
    id: int
    invoice_id: int | None = None
    email: str | None = None
    currency: str = "USD"
    item: dict[str, Any] | None = None
    customer: dict[str, Any] | None = None
