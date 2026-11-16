"""Background job runner for order fulfillment with safety guards and crash resilience."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from resell.adapters.delivery_base import DeliveryAdapter
from resell.adapters.payment_base import PaymentAdapter
from resell.adapters.supplier_base import SupplierAdapter
from resell.clock import Clock, RealClock
from resell.config import MappingConfig, Settings
from resell.guards import GuardBreachError, validate_guards
from resell.models import CreatePurchaseInput
from resell.orders import OrdersRepo
from resell.states import OrderState

logger = logging.getLogger(__name__)


@runtime_checkable
class JobRunnerProtocol(Protocol):
    """Protocol for submitting orders to background worker."""

    def submit(self, order_id: int) -> None:
        """Enqueue an order ID for background processing."""
        ...


class StubJobRunner:
    """In-memory stub job runner for webhook intake in Stage 3."""

    def __init__(self) -> None:
        self.submitted_order_ids: list[int] = []

    def submit(self, order_id: int) -> None:
        self.submitted_order_ids.append(order_id)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def resume(self) -> None:
        pass


class JobRunner:
    """Core asynchronous job engine executing order fulfillment against adapters."""

    def __init__(
        self,
        repo: OrdersRepo,
        supplier: SupplierAdapter,
        payment: PaymentAdapter,
        delivery: DeliveryAdapter,
        mapping: MappingConfig,
        settings: Settings,
        clock: Clock | None = None,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.repo = repo
        self.supplier = supplier
        self.payment = payment
        self.delivery = delivery
        self.mapping = mapping
        self.settings = settings
        self.clock = clock or RealClock()
        self.poll_interval_seconds = poll_interval_seconds

        # Global serialization lock: only ONE Litecoin payment broadcasted at a time
        self._payment_lock = asyncio.Lock()

        # In-flight task tracking: order_id -> asyncio.Task
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._daily_spent_sats: int = 0

    def submit(self, order_id: int) -> None:
        """Enqueue order for execution; dedupes if already active."""
        existing = self._tasks.get(order_id)
        if existing and not existing.done():
            logger.info("Order %d already active in runner; skipping duplicate submit.", order_id)
            return

        task = asyncio.create_task(self._process_order(order_id))
        self._tasks[order_id] = task

    async def resume(self) -> None:
        """Resume all unfinished orders from the database on startup."""
        unresumed = self.repo.orders_to_resume()
        logger.info("Resuming %d non-terminal orders on startup.", len(unresumed))
        for order in unresumed:
            self.submit(order.id)

    async def process_all_pending(self, timeout_seconds: float = 10.0) -> None:
        """Wait for all currently active tasks to complete (for tests/CLI)."""
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while True:
            active = [t for t in self._tasks.values() if not t.done()]
            if not active:
                break
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(self.poll_interval_seconds)

    async def _process_order(self, order_id: int) -> None:
        """Execute the order state progression with fail-closed semantics."""
        order = self.repo.get_order(order_id)
        if not order or order.state in (OrderState.DELIVERED, OrderState.REFUNDED):
            return

        try:
            # 1. Look up variant mapping
            variant_map = self.mapping.get_variant(order.variant_id)
            if not variant_map or not variant_map.enabled:
                logger.error(
                    "Order %d variant %d unmapped or disabled.",
                    order_id,
                    order.variant_id,
                )
                self.repo.transition(
                    order_id,
                    OrderState.REFUND_NEEDED,
                    reason=f"Unmapped variant_id={order.variant_id}",
                )
                return

            # 2. Step: RECEIVED -> CHECKOUT_CREATED
            if order.state == OrderState.RECEIVED:
                checkout_ref = await self.supplier.create_checkout(
                    domain=variant_map.supplier_domain,
                    product_slug=variant_map.supplier_product_slug,
                    variant_id=variant_map.supplier_variant_id,
                    quantity=order.quantity,
                )
                self.repo.create_purchase(
                    CreatePurchaseInput(
                        order_id=order.id,
                        supplier_domain=variant_map.supplier_domain,
                        supplier_product_slug=variant_map.supplier_product_slug,
                        supplier_variant_id=variant_map.supplier_variant_id,
                        supplier_checkout_id=checkout_ref.supplier_checkout_id,
                        ltc_address=checkout_ref.ltc_address,
                        ltc_amount_sats=checkout_ref.ltc_amount_sats,
                    )
                )
                order = self.repo.transition(
                    order_id,
                    OrderState.CHECKOUT_CREATED,
                    reason="Supplier checkout created",
                    metadata={"checkout_id": checkout_ref.supplier_checkout_id},
                )

            # 3. Step: CHECKOUT_CREATED -> PAID
            purchase = self.repo.get_purchase(order_id)
            if not purchase:
                raise RuntimeError(f"Order {order_id} has no purchase record.")

            if order.state == OrderState.CHECKOUT_CREATED:
                # Run safety guards before payment
                wallet_bal = await self.payment.balance_sats()
                supplier_cost_usd = Decimal("0.00")
                try:
                    supplier_variants = await self.supplier.list_variants(
                        domain=variant_map.supplier_domain,
                        product_slug=variant_map.supplier_product_slug,
                    )
                    if supplier_variants:
                        matching = next(
                            (
                                v
                                for v in supplier_variants
                                if str(v.id) == str(variant_map.supplier_variant_id)
                            ),
                            supplier_variants[0],
                        )
                        supplier_cost_usd = matching.price * order.quantity
                except Exception as exc:
                    logger.warning("Could not fetch supplier variants for cost: %s", exc)
                    supplier_cost_usd = variant_map.max_price_usd * order.quantity

                min_margin = variant_map.min_margin_usd or self.settings.MARGIN_FLOOR_USD
                validate_guards(
                    sale_price_usd=order.price_paid_usd,
                    supplier_cost_usd=supplier_cost_usd,
                    margin_floor_usd=min_margin,
                    wallet_balance_sats=wallet_bal,
                    required_sats=purchase.ltc_amount_sats or 0,
                    min_wallet_floor_sats=self.settings.MIN_WALLET_BALANCE_SATS,
                    daily_spent_sats=self._daily_spent_sats,
                    max_spend_per_order_sats=self.settings.MAX_SPEND_PER_ORDER_SATS,
                    max_spend_per_day_sats=self.settings.MAX_SPEND_PER_DAY_SATS,
                )

                # Acquire global payment lock to serialize Litecoin transactions
                async with self._payment_lock:
                    # Double-check if crash recovery already paid
                    fresh_purchase = self.repo.get_purchase(order_id)
                    if fresh_purchase and fresh_purchase.txid:
                        txid = fresh_purchase.txid
                    else:
                        assert purchase.ltc_address and purchase.ltc_amount_sats
                        txid = await self.payment.pay(
                            address=purchase.ltc_address,
                            amount_sats=purchase.ltc_amount_sats,
                            order_id=order.id,
                        )
                        self._daily_spent_sats += purchase.ltc_amount_sats
                        self.repo.update_purchase(order.id, txid=txid)

                order = self.repo.transition(
                    order_id,
                    OrderState.PAID,
                    reason="Litecoin payment broadcasted",
                    metadata={"txid": txid},
                )

            # 4. Step: PAID / DELAYED -> Wait for confirmation
            assert purchase.supplier_checkout_id is not None
            created_dt = datetime.fromisoformat(purchase.created_at)

            while True:
                status = await self.supplier.get_status(
                    domain=variant_map.supplier_domain,
                    checkout_id=purchase.supplier_checkout_id,
                )
                if status == "completed":
                    break

                # Evaluate timeouts using clock
                elapsed = (self.clock.now() - created_dt).total_seconds()

                if elapsed > self.settings.GIVEUP_THRESHOLD_SECONDS:
                    logger.error("Order %d exceeded give-up threshold (%.1fs).", order_id, elapsed)
                    self.repo.transition(
                        order_id,
                        OrderState.REFUND_NEEDED,
                        reason=f"Confirmation giveup threshold exceeded ({elapsed:.1f}s)",
                    )
                    return

                is_delayed = elapsed > self.settings.DELAYED_THRESHOLD_SECONDS
                if is_delayed and order.state != OrderState.DELAYED:
                    logger.warning("Order %d confirmation delayed (%.1fs).", order_id, elapsed)
                    order = self.repo.transition(
                        order_id,
                        OrderState.DELAYED,
                        reason=f"Delayed past {self.settings.DELAYED_THRESHOLD_SECONDS}s",
                    )
                    await self.delivery.notify_delayed(order)

                await asyncio.sleep(self.poll_interval_seconds)

            # 5. Step: Fetch items
            items = await self.supplier.fetch_items(
                domain=variant_map.supplier_domain,
                checkout_id=purchase.supplier_checkout_id,
            )

            # Validate deliverables
            if not items or len(items) < order.quantity:
                count = len(items) if items else 0
                logger.error(
                    "Order %d incomplete items: received %d, expected %d",
                    order_id,
                    count,
                    order.quantity,
                )
                self.repo.transition(
                    order_id,
                    OrderState.REFUND_NEEDED,
                    reason=f"Incomplete supplier delivery ({count}/{order.quantity})",
                )
                return

            self.repo.update_purchase(order.id, items_delivered=items)
            order = self.repo.transition(
                order_id,
                OrderState.FETCHED,
                reason="Supplier delivered items",
                metadata={"count": len(items)},
            )

            # 6. Step: Deliver to customer
            await self.delivery.deliver(order, items)
            self.repo.transition(
                order_id,
                OrderState.DELIVERED,
                reason="Items delivered to SellAuth invoice",
            )
            logger.info("Order %d delivered successfully.", order_id)

        except GuardBreachError as exc:
            logger.error("Order %d failed guard checks: %s", order_id, exc)
            self.repo.transition(order_id, OrderState.REFUND_NEEDED, reason=str(exc))
        except Exception as exc:
            logger.exception("Order %d unhandled exception in runner: %s", order_id, exc)
            self.repo.transition(
                order_id,
                OrderState.REFUND_NEEDED,
                reason=f"Unhandled exception: {exc}",
            )
