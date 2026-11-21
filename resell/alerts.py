"""Discord webhook alerting and monitoring system per Stage 10.

Strict constraints:
- Minimalist, professional dashboard look.
- STRICTLY ASCII ONLY: NO emojis, NO Unicode symbols.
- Clean Discord Embeds with standard Markdown.
"""

from __future__ import annotations

import datetime
import logging
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr

from resell.logging_setup import redact_text

if TYPE_CHECKING:
    from resell.config import Settings
    from resell.models import Order, Purchase

logger = logging.getLogger(__name__)

# Discord embed color constants (clean professional hex palette)
COLOR_SYSTEM = 0x34495E     # Dark Slate (System / Lifecycle)
COLOR_ORDER = 0x2980B9      # Steel Blue (Orders)
COLOR_SUPPLIER = 0x8E44AD   # Dark Purple (Supplier / Crypto)
COLOR_DELIVERED = 0x27AE60  # Forest Green (Delivery & Profit)
COLOR_WARNING = 0xF39C12    # Amber (Warnings & Thresholds)
COLOR_CRITICAL = 0xC0392B   # Deep Red (Critical & Killswitch)

# Minimum cooldown between low balance alerts (12 hours)
LOW_BALANCE_COOLDOWN_SECONDS = 12 * 3600


def sanitize_ascii(text: str) -> str:
    """Strip any non-ASCII characters to guarantee strict ASCII output."""
    if not text:
        return ""
    # Filter strictly to printable ASCII (32-126) plus newline and tab
    clean = "".join(c for c in text if (32 <= ord(c) <= 126) or c in "\n\t")
    # Replace multiple consecutive spaces with a single space
    clean = re.sub(r"[ \t]+", " ", clean)
    return clean.strip()


def mask_item(item: str) -> str:
    """Mask delivered digital item tokens cleanly for Discord presentation."""
    s = str(item).strip()
    if len(s) <= 8:
        return f"{s[:2]}..." if len(s) > 2 else "..."
    return f"{s[:4]}...{s[-4:]}"


class DiscordAlerts:
    """Manages routing of structured Discord embed alerts to dedicated channels."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._custom_client = http_client

        # Channel webhook URLs
        self.webhook_orders = self._get_secret(settings.DISCORD_WEBHOOK_ORDERS) or self._get_secret(
            settings.ALERT_WEBHOOK_URL
        )
        self.webhook_supplier = self._get_secret(
            settings.DISCORD_WEBHOOK_SUPPLIER
        ) or self.webhook_orders
        self.webhook_delivered = self._get_secret(
            settings.DISCORD_WEBHOOK_DELIVERED
        ) or self.webhook_orders
        self.webhook_alerts = self._get_secret(
            settings.DISCORD_WEBHOOK_ALERTS
        ) or self.webhook_orders

        self.admin_role_id = settings.DISCORD_ADMIN_ROLE_ID

        # Cooldown tracking
        self._last_low_balance_alert_ts: float | None = None

    @staticmethod
    def _get_secret(field: SecretStr | str | None) -> str | None:
        """Extract string representation from SecretStr or str."""
        if field is None:
            return None
        val = field.get_secret_value() if isinstance(field, SecretStr) else str(field)
        val = val.strip()
        return val if val else None

    async def _send_payload(self, webhook_url: str | None, payload: dict[str, Any]) -> bool:
        """Send formatted payload to Discord webhook asynchronously."""
        if not webhook_url:
            return False

        # If webhook is a dummy/fake URL, skip network call immediately
        lower_url = webhook_url.lower()
        if "fake" in lower_url or "example.com" in lower_url:
            logger.debug(
                "Discord webhook URL is a placeholder (%s); skipping HTTP call.",
                webhook_url,
            )
            return False

        headers = {"Content-Type": "application/json"}
        try:
            if self._custom_client is not None:
                resp = await self._custom_client.post(webhook_url, json=payload, headers=headers)
                return resp.status_code in (200, 204)

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook_url, json=payload, headers=headers)
                if resp.status_code not in (200, 204):
                    logger.warning(
                        "Discord webhook returned HTTP %d: %s",
                        resp.status_code,
                        resp.text,
                    )
                    return False
                return True
        except Exception as exc:
            logger.warning("Failed to send Discord webhook: %s", exc)
            return False

    def _build_embed(
        self,
        title: str,
        color: int,
        fields: list[tuple[str, str, bool]],
        description: str | None = None,
        footer: str = "ResellAuth Monitor",
    ) -> dict[str, Any]:
        """Construct a validated Discord embed with pure ASCII formatting."""
        embed: dict[str, Any] = {
            "title": sanitize_ascii(title),
            "color": color,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "footer": {"text": sanitize_ascii(footer)},
        }
        if description:
            embed["description"] = sanitize_ascii(redact_text(description))

        embed_fields = []
        for name, value, inline in fields:
            val_str = str(value)
            embed_fields.append(
                {
                    "name": sanitize_ascii(name),
                    "value": sanitize_ascii(redact_text(val_str)),
                    "inline": inline,
                }
            )
        embed["fields"] = embed_fields
        return embed

    async def send_order_received(
        self,
        order: Order,
        variant_name: str | None = None,
    ) -> bool:
        """Log incoming order event to DISCORD_WEBHOOK_ORDERS."""
        title = "[ORDER RECEIVED]"
        var_display = variant_name or f"Variant #{order.variant_id}"
        fields = [
            ("Invoice ID", f"#{order.invoice_id}", True),
            ("Order ID", f"#{order.id}", True),
            ("Variant", var_display, True),
            ("Quantity", str(order.quantity), True),
            ("Price Paid", f"${order.price_paid_usd:.2f} {order.currency}", True),
            ("Buyer Email", order.email, True),
        ]
        embed = self._build_embed(title, COLOR_ORDER, fields)
        return await self._send_payload(self.webhook_orders, {"embeds": [embed]})

    async def send_supplier_purchase(
        self,
        order: Order,
        purchase: Purchase,
    ) -> bool:
        """Log supplier purchase and payment broadcast to DISCORD_WEBHOOK_SUPPLIER."""
        title = "[SUPPLIER PURCHASE]"
        sats = purchase.ltc_amount_sats or 0
        ltc_amount = Decimal(sats) / Decimal("100000000")
        txid_str = purchase.txid or "Pending"

        fields = [
            ("Order ID", f"#{order.id} (Invoice #{order.invoice_id})", True),
            ("Supplier Domain", purchase.supplier_domain, True),
            ("Checkout ID", purchase.supplier_checkout_id or "N/A", True),
            ("LTC Amount", f"{sats} sats ({ltc_amount:.8f} LTC)", True),
            ("Destination Address", purchase.ltc_address or "N/A", False),
            ("Transaction ID", f"`{txid_str}`", False),
        ]
        embed = self._build_embed(title, COLOR_SUPPLIER, fields)
        return await self._send_payload(self.webhook_supplier, {"embeds": [embed]})

    async def send_order_delivered(
        self,
        order: Order,
        items: list[str],
        elapsed_seconds: float,
        profit_usd: Decimal,
        supplier_cost_usd: Decimal,
    ) -> bool:
        """Log successful customer delivery and profit to DISCORD_WEBHOOK_DELIVERED."""
        title = "[ORDER DELIVERED]"
        masked_items = [mask_item(it) for it in items]
        items_preview = ", ".join(masked_items) if masked_items else "None"

        fields = [
            ("Invoice ID", f"#{order.invoice_id}", True),
            ("Order ID", f"#{order.id}", True),
            ("Time Taken", f"{elapsed_seconds:.1f}s", True),
            ("Customer Paid", f"${order.price_paid_usd:.2f}", True),
            ("Supplier Cost", f"${supplier_cost_usd:.2f}", True),
            ("Net Profit", f"${profit_usd:.2f}", True),
            ("Delivered Items", f"`{items_preview}`", False),
        ]
        embed = self._build_embed(title, COLOR_DELIVERED, fields)
        return await self._send_payload(self.webhook_delivered, {"embeds": [embed]})

    async def send_alert(
        self,
        title: str,
        message: str,
        error: Exception | str | None = None,
        is_critical: bool = False,
    ) -> bool:
        """Log system error, guard breach, or warning to DISCORD_WEBHOOK_ALERTS."""
        clean_title = f"[CRITICAL] {title}" if is_critical else f"[ALERT] {title}"
        color = COLOR_CRITICAL if is_critical else COLOR_WARNING

        fields = [("Details", message, False)]
        if error is not None:
            err_str = str(error)
            fields.append(("Traceback / Error", f"```{err_str[:900]}```", False))

        embed = self._build_embed(clean_title, color, fields)
        payload: dict[str, Any] = {"embeds": [embed]}

        # If critical and admin role configured, ping the role
        if is_critical and self.admin_role_id:
            role_ping = f"<@&{self.admin_role_id.strip()}>"
            payload["content"] = role_ping

        return await self._send_payload(self.webhook_alerts, payload)

    async def send_low_balance_alert(
        self,
        current_balance_sats: int,
        floor_sats: int,
        now_ts: float | None = None,
    ) -> bool:
        """Send low wallet balance alert with 12-hour deduplication cooldown."""
        import time

        current_time = now_ts if now_ts is not None else time.time()
        if (
            self._last_low_balance_alert_ts is not None
            and (current_time - self._last_low_balance_alert_ts) < LOW_BALANCE_COOLDOWN_SECONDS
        ):
            logger.info("Low balance alert suppressed due to 12h cooldown.")
            return False

        self._last_low_balance_alert_ts = current_time
        curr_ltc = Decimal(current_balance_sats) / Decimal("100000000")
        floor_ltc = Decimal(floor_sats) / Decimal("100000000")

        return await self.send_alert(
            title="Low Electrum-LTC Wallet Balance",
            message=(
                f"Wallet balance is {current_balance_sats} sats ({curr_ltc:.6f} LTC), "
                f"which is below the safety floor of {floor_sats} sats ({floor_ltc:.6f} LTC). "
                "Purchases may fail closed until wallet is funded."
            ),
            is_critical=True,
        )

    async def send_startup(self, variant_count: int) -> bool:
        """Send clean [SYSTEM STARTUP] embed on app boot to DISCORD_WEBHOOK_ORDERS."""
        title = "[SYSTEM STARTUP]"
        fields = [
            ("Status", "Operational", True),
            ("Loaded Variants", str(variant_count), True),
            (
                "Sync Interval",
                f"{self.settings.STOCK_SYNC_INTERVAL_MINUTES}m (+/- 30s jitter)",
                True,
            ),
        ]
        embed = self._build_embed(title, COLOR_SYSTEM, fields)
        return await self._send_payload(self.webhook_orders, {"embeds": [embed]})

    async def send_shutdown(self) -> bool:
        """Send clean [SYSTEM SHUTDOWN] embed on app termination to DISCORD_WEBHOOK_ORDERS."""
        title = "[SYSTEM SHUTDOWN]"
        now_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        fields = [
            ("Status", "Terminated Gracefully", True),
            ("Timestamp", now_str, True),
        ]
        embed = self._build_embed(title, COLOR_SYSTEM, fields)
        return await self._send_payload(self.webhook_orders, {"embeds": [embed]})

    async def send_daily_report(
        self,
        total_orders: int,
        total_revenue_usd: Decimal,
        total_supplier_cost_usd: Decimal,
        total_profit_usd: Decimal,
    ) -> bool:
        """Send daily reconciliation summary embed to DISCORD_WEBHOOK_DELIVERED."""
        title = "[DAILY PROFIT REPORT (24H)]"
        fields = [
            ("Total Orders", str(total_orders), True),
            ("Total Revenue", f"${total_revenue_usd:.2f} USD", True),
            ("Total Cost", f"${total_supplier_cost_usd:.2f} USD", True),
            ("Total Profit", f"${total_profit_usd:.2f} USD", True),
        ]
        embed = self._build_embed(title, COLOR_DELIVERED, fields)
        return await self._send_payload(self.webhook_delivered, {"embeds": [embed]})
