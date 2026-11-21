"""Stock synchronization service reflecting supplier inventory to SellAuth store."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resell.adapters.store_client import SellAuthStoreClient
    from resell.adapters.supplier_base import SupplierAdapter
    from resell.alerts import DiscordAlerts
    from resell.config import MappingConfig, VariantMapping
    from resell.orders import OrdersRepo

logger = logging.getLogger(__name__)


def compute_target_stock(
    supplier_stock: int,
    safety_margin: int = 1,
    in_flight: int = 0,
) -> int:
    """Compute storefront stock: max(0, supplier_stock - safety_margin - in_flight)."""
    effective_supplier = max(0, supplier_stock)
    margin = max(0, safety_margin)
    inflight = max(0, in_flight)
    return max(0, effective_supplier - margin - inflight)


class StockSynchronizer:
    """Synchronizes mapped product variant stock with supplier inventory."""

    def __init__(
        self,
        mapping: MappingConfig,
        store_client: SellAuthStoreClient,
        supplier: SupplierAdapter,
        orders_repo: OrdersRepo | None = None,
        alerts: DiscordAlerts | None = None,
        safety_margin: int = 1,
    ) -> None:
        self.mapping = mapping
        self.store_client = store_client
        self.supplier = supplier
        self.orders_repo = orders_repo
        self.alerts = alerts
        self.safety_margin = safety_margin
        self.consecutive_failures: dict[int, int] = {}

    async def sync_variant(self, variant_map: VariantMapping) -> int:
        """Sync a single mapped variant from supplier to store (fails closed to 0)."""
        logger.info(
            "Syncing variant: our_variant=%s -> supplier=%s/%s",
            variant_map.our_variant_id,
            variant_map.supplier_domain,
            variant_map.supplier_product_slug,
        )

        try:
            supplier_variants = await self.supplier.list_variants(
                domain=variant_map.supplier_domain,
                product_slug=variant_map.supplier_product_slug,
            )

            matched_variant = None
            for v in supplier_variants:
                if (
                    str(v.id) == str(variant_map.supplier_variant_id)
                    or v.name.lower() == variant_map.name.lower()
                    or len(supplier_variants) == 1
                ):
                    matched_variant = v
                    break

            if matched_variant is None:
                raise ValueError(
                    f"Supplier variant '{variant_map.supplier_variant_id}' not found among "
                    f"{[v.id for v in supplier_variants]}"
                )

            in_flight = (
                self.orders_repo.count_in_flight(variant_map.our_variant_id)
                if self.orders_repo is not None
                else 0
            )

            target_stock = compute_target_stock(
                supplier_stock=matched_variant.stock,
                safety_margin=self.safety_margin,
                in_flight=in_flight,
            )

            await self.store_client.update_stock(
                product_id=variant_map.our_product_id,
                variant_id=variant_map.our_variant_id,
                stock=target_stock,
            )

            # Reset consecutive failures on success
            self.consecutive_failures[variant_map.our_variant_id] = 0

            logger.info(
                "Stock synced successfully: variant=%s, supplier_stock=%s, target_stock=%s",
                variant_map.our_variant_id,
                matched_variant.stock,
                target_stock,
            )
            return target_stock

        except Exception as exc:
            v_id = variant_map.our_variant_id
            fail_count = self.consecutive_failures.get(v_id, 0) + 1
            self.consecutive_failures[v_id] = fail_count

            logger.error(
                "Stock sync failed for variant %s (failure #%d): %s",
                v_id,
                fail_count,
                exc,
            )

            # Always fail closed to 0 on store
            try:
                await self.store_client.update_stock(
                    product_id=variant_map.our_product_id,
                    variant_id=variant_map.our_variant_id,
                    stock=0,
                )
            except Exception as zero_exc:
                logger.critical(
                    "Failed to set stock to 0 during fail-closed for variant %s: %s",
                    v_id,
                    zero_exc,
                )

            # Auto-disable broken products on 3 consecutive failures
            if fail_count >= 3:
                variant_map.enabled = False
                logger.critical(
                    "Variant %s failed 3 consecutive times. Automatically disabled.",
                    v_id,
                )
                if self.alerts is not None:
                    await self.alerts.send_alert(
                        title=f"Variant Disabled: {variant_map.name}",
                        message=(
                            f"Supplier scraping failed 3 consecutive times for variant "
                            f"'{variant_map.name}' (our_variant_id={v_id}). "
                            "Store stock was set to 0 and variant has been auto-disabled."
                        ),
                        error=str(exc),
                        is_critical=True,
                    )

            raise exc

    async def sync_all(self) -> dict[int, int]:
        """Sync stock for all enabled mapped variants without stopping on single failure."""
        results: dict[int, int] = {}
        for v in list(self.mapping.variants):
            if not v.enabled:
                continue
            try:
                stock = await self.sync_variant(v)
                results[v.our_variant_id] = stock
            except Exception as exc:
                logger.warning(
                    "Error syncing variant %s (skipping to next): %s",
                    v.our_variant_id,
                    exc,
                )
                results[v.our_variant_id] = 0
        return results

    async def run_loop(
        self,
        interval_minutes: float = 5.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run continuous stock sync loop with +/- 30s jitter."""
        logger.info(
            "Starting stock synchronization loop (base interval: %.1fm with +/- 30s jitter)",
            interval_minutes,
        )
        while stop_event is None or not stop_event.is_set():
            try:
                await self.sync_all()
            except Exception as exc:
                logger.error("Stock sync cycle encountered error: %s", exc)

            # Calculate next sleep duration with +/- 30s random jitter
            base_seconds = interval_minutes * 60.0
            jitter = random.uniform(-30.0, 30.0)
            sleep_duration = max(5.0, base_seconds + jitter)

            try:
                if stop_event is not None:
                    await asyncio.wait_for(stop_event.wait(), timeout=sleep_duration)
                    break
                else:
                    await asyncio.sleep(sleep_duration)
            except TimeoutError:
                continue
