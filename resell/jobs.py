"""Background job runner for order fulfillment with safety guards and crash resilience."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from resell.adapters.delivery_base import DeliveryAdapter
from resell.adapters.payment_base import PaymentAdapter
from resell.adapters.supplier_base import SupplierAdapter
from resell.alerts import DiscordAlerts
from resell.clock import Clock, RealClock
from resell.config import MappingConfig, Settings
from resell.guards import GuardBreachError, validate_guards
from resell.killswitch import is_killswitch_active
from resell.models import CreatePurchaseInput, Order
from resell.orders import OrdersRepo
from resell.states import InvalidStateTransitionError, OrderState

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

    async def resume(self) -> list[Order]:
        return []


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
        alerts: DiscordAlerts | None = None,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.repo = repo
        self.supplier = supplier
        self.payment = payment
        self.delivery = delivery
        self.mapping = mapping
        self.settings = settings
        self.clock = clock or RealClock()
        self.alerts = alerts or DiscordAlerts(settings)
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

    async def resume(self) -> list[Order]:
        """Resume all unfinished orders from the database on startup."""
        unresumed = self.repo.orders_to_resume() or []
        logger.info("Resuming %d non-terminal orders on startup.", len(unresumed))
        for order in unresumed:
            self.submit(order.id)
        return unresumed

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

    def _safe_transition(
        self,
        order_id: int,
        target_state: OrderState,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Order | None:
        """Safely transition order state, aborting if already terminal or fail-safe."""
        current = self.repo.get_order(order_id)
        if not current:
            return None

        # Terminal and fail-safe states cannot be moved back to DELAYED or REFUND_NEEDED
        terminal_states = {
            OrderState.REFUND_NEEDED,
            OrderState.REFUNDED,
            OrderState.DELIVERED,
        }
        if current.state in terminal_states:
            if current.state == target_state:
                return current
            logger.info(
                "Order %d already in %s; ignoring transition to %s.",
                order_id,
                current.state,
                target_state,
            )
            return None

        try:
            return self.repo.transition(
                order_id,
                target_state,
                reason=reason,
                metadata=metadata,
            )
        except InvalidStateTransitionError as exc:
            logger.warning(
                "Ignoring illegal transition for order %d: %s",
                order_id,
                exc,
            )
            return None

    async def _process_order(self, order_id: int) -> None:
        """Execute the order state progression with fail-closed semantics."""
        if is_killswitch_active(self.settings):
            logger.warning(
                "[JOB-RUNNER] Killswitch active; pausing execution for order %d.",
                order_id,
            )
            return

        terminal_states = {
            OrderState.REFUND_NEEDED,
            OrderState.REFUNDED,
            OrderState.DELIVERED,
        }
        order = self.repo.get_order(order_id)
        allow_money = os.getenv("ALLOW_MONEY_TESTS")
        logger.info(
            "[JOB-RUNNER] _process_order starting: order_id=%d, state=%s, ALLOW_MONEY_TESTS=%r",
            order_id,
            order.state.value if order else None,
            allow_money,
        )
        st_val = order.state.value if order else "None"
        print(
            f"[JOB-RUNNER] Order #{order_id} fulfillment started (state: {st_val})",
            flush=True,
        )

        if not order:
            logger.info("[JOB-RUNNER] Order %d not found in database; exiting early.", order_id)
            return

        if order.state in terminal_states:
            logger.info(
                "[JOB-RUNNER] Order %d is in terminal/fail-safe state '%s'; skipping processing.",
                order_id,
                order.state.value,
            )
            return

        try:
            # 1. Look up variant mapping
            variant_map = self.mapping.get_variant(order.variant_id)
            if not variant_map or not variant_map.enabled:
                logger.error(
                    "[JOB-RUNNER] Order %d variant %d unmapped or disabled; exiting early.",
                    order_id,
                    order.variant_id,
                )
                self._safe_transition(
                    order_id,
                    OrderState.REFUND_NEEDED,
                    reason=f"Unmapped variant_id={order.variant_id}",
                )
                return

            # 2. Step: RECEIVED -> CHECKOUT_CREATED
            if order.state == OrderState.RECEIVED:
                if self.alerts is not None:
                    try:
                        await self.alerts.send_order_received(order, variant_map.name)
                    except Exception as alert_err:
                        logger.warning("Failed to send order received alert: %s", alert_err)

                logger.info(
                    "[JOB-RUNNER] Order %d in RECEIVED state; creating supplier checkout...",
                    order_id,
                )
                print(
                    f"[JOB-RUNNER] Order #{order_id} creating checkout on "
                    f"{variant_map.supplier_domain}...",
                    flush=True,
                )
                supplier_email = getattr(
                    self.settings, "SUPPLIER_EMAIL", "supplier_orders@example.com"
                )
                checkout_ref = await self.supplier.create_checkout(
                    domain=variant_map.supplier_domain,
                    product_slug=variant_map.supplier_product_slug,
                    variant_id=variant_map.supplier_variant_id,
                    quantity=order.quantity,
                    email=supplier_email,
                )
                print(
                    f"[JOB-RUNNER] Order #{order_id} checkout: {checkout_ref.supplier_checkout_id} "
                    f"({checkout_ref.ltc_amount_sats} sats -> {checkout_ref.ltc_address})",
                    flush=True,
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
                new_order = self._safe_transition(
                    order_id,
                    OrderState.CHECKOUT_CREATED,
                    reason="Supplier checkout created",
                    metadata={"checkout_id": checkout_ref.supplier_checkout_id},
                )
                if not new_order:
                    logger.info(
                        "[JOB-RUNNER] Order %d failed transition to CHECKOUT_CREATED; exiting.",
                        order_id,
                    )
                    return
                order = new_order

            # 3. Step: CHECKOUT_CREATED -> PAID
            purchase = self.repo.get_purchase(order_id)
            if not purchase:
                raise RuntimeError(f"Order {order_id} has no purchase record.")

            if order.state == OrderState.CHECKOUT_CREATED:
                logger.info(
                    "[JOB-RUNNER] Order %d in CHECKOUT_CREATED state; validating safety guards...",
                    order_id,
                )
                # Run safety guards before payment
                wallet_bal = await self.payment.balance_sats()
                if wallet_bal < self.settings.MIN_WALLET_BALANCE_SATS and self.alerts is not None:
                    try:
                        await self.alerts.send_low_balance_alert(
                            current_balance_sats=wallet_bal,
                            floor_sats=self.settings.MIN_WALLET_BALANCE_SATS,
                        )
                    except Exception as bal_err:
                        logger.warning("Failed to send low balance alert: %s", bal_err)

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
                        logger.info(
                            "[JOB-RUNNER] Order %d already paid with txid=%s (skipping broadcast)",
                            order_id,
                            txid,
                        )
                    else:
                        assert purchase.ltc_address and purchase.ltc_amount_sats
                        logger.info(
                            "[JOB-RUNNER] Calling payment.pay(): order_id=%d, address=%s, "
                            "sats=%d, ALLOW_MONEY_TESTS=%r",
                            order.id,
                            purchase.ltc_address,
                            purchase.ltc_amount_sats,
                            allow_money,
                        )
                        print(
                            f"[JOB-RUNNER] Order #{order.id} broadcasting payment: "
                            f"{purchase.ltc_amount_sats} sats to {purchase.ltc_address}...",
                            flush=True,
                        )
                        txid = await self.payment.pay(
                            address=purchase.ltc_address,
                            amount_sats=purchase.ltc_amount_sats,
                            order_id=order.id,
                        )
                        logger.info(
                            "[JOB-RUNNER] Payment broadcast returned txid: %r",
                            txid,
                        )
                        print(
                            f"[JOB-RUNNER] Order #{order.id} payment broadcast SUCCESS: "
                            f"txid={txid}",
                            flush=True,
                        )
                        # Strictly verify txid is a valid, non-empty string
                        if not txid or not isinstance(txid, str) or not txid.strip():
                            raise RuntimeError(
                                f"Order {order_id} payment returned empty txid (got {txid!r})"
                            )
                        clean_txid = txid.strip()
                        self._daily_spent_sats += purchase.ltc_amount_sats
                        self.repo.update_purchase(order.id, txid=clean_txid)
                        txid = clean_txid
                        if self.alerts is not None:
                            fresh_p = self.repo.get_purchase(order.id)
                            if fresh_p:
                                try:
                                    await self.alerts.send_supplier_purchase(order, fresh_p)
                                except Exception as alert_err:
                                    logger.warning(
                                        "Failed to send supplier purchase alert: %s",
                                        alert_err,
                                    )

                # Only transition to PAID if txid is a valid, non-empty string
                if not txid or not isinstance(txid, str) or not txid.strip():
                    raise RuntimeError(
                        f"Order {order_id} invalid txid={txid!r}; aborting transition to PAID."
                    )

                new_order = self._safe_transition(
                    order_id,
                    OrderState.PAID,
                    reason="Litecoin payment broadcasted",
                    metadata={"txid": txid},
                )
                if not new_order:
                    logger.info(
                        "[JOB-RUNNER] Order %d failed transition to PAID; exiting early.",
                        order_id,
                    )
                    return
                order = new_order

            # 4. Step: PAID / DELAYED -> Wait for confirmation
            assert purchase.supplier_checkout_id is not None
            created_dt = datetime.fromisoformat(purchase.created_at)

            while True:
                # Check DB state at start of each poll iteration
                current_order = self.repo.get_order(order_id)
                if not current_order or current_order.state in terminal_states:
                    logger.info(
                        "Order %d in terminal/fail-safe state %s; stopping polling.",
                        order_id,
                        current_order.state if current_order else "None",
                    )
                    return

                try:
                    status = await self.supplier.get_status(
                        domain=variant_map.supplier_domain,
                        checkout_id=purchase.supplier_checkout_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Error querying supplier status for order %d: %s",
                        order_id,
                        exc,
                    )
                    status = "pending"

                if status == "completed":
                    break

                # Re-check state before evaluating timeouts
                current_order = self.repo.get_order(order_id)
                if not current_order or current_order.state in terminal_states:
                    logger.info(
                        "[JOB-RUNNER] Order %d entered %s; exiting polling loop.",
                        order_id,
                        current_order.state.value if current_order else "None",
                    )
                    return

                # Evaluate timeouts using clock
                elapsed = (self.clock.now() - created_dt).total_seconds()

                if elapsed > self.settings.GIVEUP_THRESHOLD_SECONDS:
                    logger.error(
                        "Order %d exceeded give-up threshold (%.1fs).",
                        order_id,
                        elapsed,
                    )
                    self._safe_transition(
                        order_id,
                        OrderState.REFUND_NEEDED,
                        reason=f"Confirmation giveup threshold exceeded ({elapsed:.1f}s)",
                    )
                    return

                is_delayed = elapsed > self.settings.DELAYED_THRESHOLD_SECONDS
                if is_delayed and current_order.state != OrderState.DELAYED:
                    # Abort delayed timer if order entered refund_needed or refunded
                    fresh_order = self.repo.get_order(order_id)
                    if not fresh_order or fresh_order.state in (
                        OrderState.REFUND_NEEDED,
                        OrderState.REFUNDED,
                        OrderState.DELIVERED,
                    ):
                        logger.info(
                            "Order %d already terminal (%s); aborting DELAYED timer.",
                            order_id,
                            fresh_order.state if fresh_order else "None",
                        )
                        return

                    logger.warning("Order %d confirmation delayed (%.1fs).", order_id, elapsed)
                    new_order = self._safe_transition(
                        order_id,
                        OrderState.DELAYED,
                        reason=f"Delayed past {self.settings.DELAYED_THRESHOLD_SECONDS}s",
                    )
                    if new_order:
                        order = new_order
                        await self.delivery.notify_delayed(order)
                    else:
                        return

                await asyncio.sleep(self.poll_interval_seconds)

            # 5. Step: Fetch items
            current_order = self.repo.get_order(order_id)
            if not current_order or current_order.state in terminal_states:
                logger.info(
                    "[JOB-RUNNER] Order %d entered %s before fetch_items; exiting.",
                    order_id,
                    current_order.state.value if current_order else "None",
                )
                return

            items = await self.supplier.fetch_items(
                domain=variant_map.supplier_domain,
                checkout_id=purchase.supplier_checkout_id,
            )
            item_cnt = len(items) if items else 0
            print(
                f"[JOB-RUNNER] Order #{order_id} fetched {item_cnt} items from supplier.",
                flush=True,
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
                self._safe_transition(
                    order_id,
                    OrderState.REFUND_NEEDED,
                    reason=f"Incomplete supplier delivery ({count}/{order.quantity})",
                )
                return

            self.repo.update_purchase(order.id, items_delivered=items)
            new_order = self._safe_transition(
                order_id,
                OrderState.FETCHED,
                reason="Supplier delivered items",
                metadata={"count": len(items)},
            )
            if not new_order:
                logger.info(
                    "[JOB-RUNNER] Order %d failed transition to FETCHED; exiting.",
                    order_id,
                )
                return
            order = new_order

            # 6. Step: Deliver to customer
            current_order = self.repo.get_order(order_id)
            if not current_order or current_order.state in terminal_states:
                logger.info(
                    "[JOB-RUNNER] Order %d entered %s before delivery; exiting.",
                    order_id,
                    current_order.state.value if current_order else "None",
                )
                return

            print(
                f"[JOB-RUNNER] Order #{order_id} delivering to invoice #{order.invoice_id}...",
                flush=True,
            )
            await self.delivery.deliver(order, items)
            self._safe_transition(
                order_id,
                OrderState.DELIVERED,
                reason="Items delivered to SellAuth invoice",
            )
            logger.info("Order %d delivered successfully.", order_id)
            print(
                f"[JOB-RUNNER] Order #{order_id} delivered to invoice #{order.invoice_id}!",
                flush=True,
            )

            # Send delivery & profit notification to DISCORD_WEBHOOK_DELIVERED
            if self.alerts is not None:
                try:
                    elapsed = (self.clock.now() - created_dt).total_seconds()
                    profit = order.price_paid_usd - supplier_cost_usd
                    await self.alerts.send_order_delivered(
                        order=order,
                        items=items,
                        elapsed_seconds=elapsed,
                        profit_usd=profit,
                        supplier_cost_usd=supplier_cost_usd,
                    )
                except Exception as alert_err:
                    logger.warning("Failed to send order delivered alert: %s", alert_err)

        except GuardBreachError as exc:
            logger.error("Order %d failed guard checks: %s", order_id, exc)
            print(f"[JOB-RUNNER] Order #{order_id} GUARD BREACH: {exc}", flush=True)
            if self.alerts is not None:
                try:
                    await self.alerts.send_alert(
                        title=f"Guard Breach on Order #{order_id}",
                        message=str(exc),
                        is_critical=True,
                    )
                except Exception as alert_err:
                    logger.warning("Failed to send guard breach alert: %s", alert_err)

            curr = self.repo.get_order(order_id)
            if curr and curr.state not in (
                OrderState.REFUND_NEEDED,
                OrderState.REFUNDED,
                OrderState.DELIVERED,
            ):
                self._safe_transition(order_id, OrderState.REFUND_NEEDED, reason=str(exc))
        except Exception as exc:
            logger.exception("Order %d unhandled exception in runner: %s", order_id, exc)
            print(f"[JOB-RUNNER] Order #{order_id} ERROR: {exc}", flush=True)
            if self.alerts is not None:
                try:
                    await self.alerts.send_alert(
                        title=f"Order #{order_id} Processing Error",
                        message=str(exc),
                        error=exc,
                        is_critical=True,
                    )
                except Exception as alert_err:
                    logger.warning("Failed to send processing error alert: %s", alert_err)

            curr = self.repo.get_order(order_id)
            if curr and curr.state not in (
                OrderState.REFUND_NEEDED,
                OrderState.REFUNDED,
                OrderState.DELIVERED,
            ):
                self._safe_transition(
                    order_id,
                    OrderState.REFUND_NEEDED,
                    reason=f"Unhandled exception: {exc}",
                )
