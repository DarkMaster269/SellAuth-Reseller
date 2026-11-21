"""Command-line interface for resell-sellauth."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import urllib.parse
from decimal import Decimal

from resell.adapters.delivery_late import LateDeliveryAdapter
from resell.adapters.payment_electrum import (
    ElectrumConnectionError,
    ElectrumPaymentAdapter,
    ElectrumRPCError,
    PaymentBroadcastError,
)
from resell.adapters.store_client import SellAuthStoreClient, StoreAuthError
from resell.adapters.supplier_scrape import (
    SupplierParseError,
    SupplierScraper,
    SupplierUnavailableError,
)
from resell.alerts import DiscordAlerts
from resell.config import load_mapping, load_settings
from resell.db import Database
from resell.jobs import JobRunner
from resell.killswitch import activate_killswitch, deactivate_killswitch
from resell.logging_setup import setup_logging
from resell.models import CreateOrderInput, CreatePurchaseInput, Variant
from resell.orders import OrdersRepo
from resell.states import OrderState
from resell.stock_sync import StockSynchronizer

logger = logging.getLogger("resell.cli")


def parse_supplier_url(url: str) -> tuple[str, str]:
    """Extract domain and product slug from a supplier URL or path."""
    clean_url = url.strip()
    if not clean_url:
        raise ValueError("URL cannot be empty.")
    if not clean_url.startswith(("http://", "https://")):
        clean_url = f"https://{clean_url}"

    parsed = urllib.parse.urlparse(clean_url)
    domain = parsed.netloc or ""
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not domain or not path_parts:
        raise ValueError(f"Invalid supplier URL: {url}")

    # Standard format: /product/{slug}
    if path_parts[0] == "product" and len(path_parts) >= 2:
        slug = path_parts[1]
    else:
        slug = path_parts[-1]

    return domain, slug


def format_variants_table(domain: str, slug: str, variants: list[Variant]) -> str:
    """Format extracted product variants into a readable ASCII table."""
    lines = [
        f"\nInspecting: https://{domain}/product/{slug}",
        f"Found {len(variants)} variant(s):\n",
        f"{'ID':<12} | {'Name':<35} | {'Price (USD)':<12} | {'Stock':<8} | {'Min/Max Qty':<12}",
        "-" * 88,
    ]
    for v in variants:
        min_max = f"{v.min_quantity} / {v.max_quantity if v.max_quantity is not None else 'inf'}"
        price_str = f"${v.price:.2f}"
        lines.append(
            f"{v.id:<12} | {v.name[:35]:<35} | {price_str:<12} | {v.stock:<8} | {min_max:<12}"
        )
    lines.append("-" * 88)
    return "\n".join(lines)


async def run_inspect(url: str) -> str:
    """Fetch live supplier variants and return formatted summary table."""
    domain, slug = parse_supplier_url(url)
    scraper = SupplierScraper()
    variants = await scraper.list_variants(domain, slug)
    return format_variants_table(domain, slug, variants)


async def run_sync() -> dict[int, int]:
    """Execute a single pass stock synchronization for all mapped variants."""
    settings = load_settings()
    mapping = load_mapping(settings.MAPPING_FILE)

    store_client = SellAuthStoreClient(
        api_key=settings.SELLAUTH_API_KEY.get_secret_value(),
        shop_id=settings.SELLAUTH_SHOP_ID,
    )
    supplier = SupplierScraper()
    db = Database(settings.SQLITE_PATH)
    orders_repo = OrdersRepo(db)

    synchronizer = StockSynchronizer(
        mapping=mapping,
        store_client=store_client,
        supplier=supplier,
        orders_repo=orders_repo,
    )

    return await synchronizer.sync_all()


def handle_sync(args: argparse.Namespace) -> int:
    """CLI handler for the sync command."""
    try:
        results = asyncio.run(run_sync())
        print("\nStock Synchronization Results:")
        print("-" * 50)
        for variant_id, stock in results.items():
            print(f"Store Variant {variant_id:<12} -> Synced Stock: {stock}")
        print("-" * 50)
        return 0
    except StoreAuthError as exc:
        print(f"\n[ERROR] SellAuth API authentication error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[ERROR] Stock sync failed: {exc}", file=sys.stderr)
        return 1


async def run_pay_test(
    destination_address: str | None = None,
    amount_sats: int = 50000,
) -> tuple[int, str | None]:
    """Test Electrum-LTC wallet connectivity and optionally send a test transaction."""
    import os

    settings = load_settings()
    adapter = ElectrumPaymentAdapter(
        rpc_url=settings.ELECTRUM_RPC_URL,
        rpc_user=settings.ELECTRUM_RPC_USER,
        rpc_password=settings.ELECTRUM_RPC_PASSWORD,
    )

    balance = await adapter.balance_sats()
    txid = None

    if destination_address:
        if os.getenv("ALLOW_MONEY_TESTS") != "1":
            raise PermissionError(
                "Broadcasting a real payment requires ALLOW_MONEY_TESTS=1 environment variable."
            )
        txid = await adapter.pay(
            address=destination_address,
            amount_sats=amount_sats,
            order_id=0,
        )

    return balance, txid


def handle_pay_test(args: argparse.Namespace) -> int:
    """CLI handler for the pay-test command."""
    from decimal import Decimal

    try:
        balance, txid = asyncio.run(
            run_pay_test(
                destination_address=args.address,
                amount_sats=args.sats,
            )
        )
        balance_ltc = Decimal(balance) / Decimal("100000000")
        print(f"\nElectrum-LTC Wallet Balance: {balance} Satoshis ({balance_ltc:.8f} LTC)")

        if txid:
            print(f"Payment Broadcast Successfully! TXID: {txid}")
        elif args.address:
            print("[INFO] Destination provided but transaction was not broadcast.")
        else:
            print("[INFO] Wallet is reachable and healthy (no address specified for payment test).")

        return 0
    except (
        ElectrumConnectionError,
        ElectrumRPCError,
        PaymentBroadcastError,
        PermissionError,
    ) as exc:
        print(f"\n[ERROR] Electrum operation failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[ERROR] Unexpected error: {exc}", file=sys.stderr)
        return 1


async def run_buy_test(
    variant_id: int | str | None = None,
    quantity: int = 1,
    email: str | None = None,
) -> None:
    """Execute test purchase from supplier storefront and handle kill-and-resume."""
    settings = load_settings()
    mapping = load_mapping(settings.MAPPING_FILE)

    target_variant = None
    if variant_id is not None:
        target_variant = mapping.find_variant(variant_id)
        if not target_variant:
            available = [
                f"- {v.name}: our_variant_id={v.our_variant_id}, "
                f"supplier_variant_id={v.supplier_variant_id}"
                for v in mapping.variants
            ]
            raise ValueError(
                f"Variant ID '{variant_id}' not found in mapping.yaml.\n"
                f"Configured variants in mapping.yaml:\n" + "\n".join(available)
            )
    else:
        for v in mapping.variants:
            if v.enabled:
                target_variant = v
                break

    if not target_variant:
        raise ValueError("No enabled variants found in mapping.yaml")

    target_email = (
        email.strip()
        if (email and email.strip())
        else (os.getenv("BUY_TEST_EMAIL") or "buyer@gmail.com")
    )

    print(f"\n[BUY-TEST] Initiating test purchase for variant: {target_variant.name}")
    print(f"Supplier: {target_variant.supplier_domain} / {target_variant.supplier_product_slug}")
    print(f"Variant ID: {target_variant.supplier_variant_id}, Quantity: {quantity}")
    print(f"Buyer Email: {target_email}")

    db = Database(settings.SQLITE_PATH)
    db.apply_migrations()
    repo = OrdersRepo(db)
    supplier = SupplierScraper()

    dummy_invoice_id = int(time.time_ns() // 1_000_000)
    order, _ = repo.create_order_if_new(
        CreateOrderInput(
            invoice_id=dummy_invoice_id,
            item_id=dummy_invoice_id,
            product_id=target_variant.our_product_id,
            variant_id=target_variant.our_variant_id,
            quantity=quantity,
            email=target_email,
            price_paid_usd=target_variant.max_price_usd * quantity,
            currency="USD",
        )
    )
    print(f"Created local test Order ID #{order.id} (Invoice #{dummy_invoice_id})")

    supplier_email = getattr(settings, "SUPPLIER_EMAIL", "supplier_orders@example.com")
    print(f"Creating checkout invoice at supplier with supplier email: {supplier_email}...")
    checkout_ref = await supplier.create_checkout(
        domain=target_variant.supplier_domain,
        product_slug=target_variant.supplier_product_slug,
        variant_id=target_variant.supplier_variant_id,
        quantity=quantity,
        email=supplier_email,
    )
    print(f"Supplier Checkout Created: ID={checkout_ref.supplier_checkout_id}")
    print(f"LTC Address: {checkout_ref.ltc_address}")
    print(f"LTC Amount: {checkout_ref.ltc_amount_sats} Satoshis")

    repo.create_purchase(
        CreatePurchaseInput(
            order_id=order.id,
            supplier_domain=target_variant.supplier_domain,
            supplier_product_slug=target_variant.supplier_product_slug,
            supplier_variant_id=target_variant.supplier_variant_id,
            supplier_checkout_id=checkout_ref.supplier_checkout_id,
            ltc_address=checkout_ref.ltc_address,
            ltc_amount_sats=checkout_ref.ltc_amount_sats,
        )
    )
    repo.transition(
        order.id,
        OrderState.CHECKOUT_CREATED,
        reason="Supplier checkout created via buy-test",
        metadata={"checkout_id": checkout_ref.supplier_checkout_id},
    )

    print("\n" + "=" * 70)
    print("[KILL-AND-RESUME DRILL READY]")
    print(f"Order #{order.id} created and saved in SQLite with state 'checkout_created'.")
    print(f"Supplier Invoice/Checkout ID: {checkout_ref.supplier_checkout_id}")
    print(f"Locked Payment Destination: {checkout_ref.ltc_address}")
    print(f"Amount: {checkout_ref.ltc_amount_sats} Satoshis")
    print("\nTo complete the purchase and broadcast payment via Electrum-LTC:")
    print("  1. Kill this process or close this terminal to verify crash safety.")
    print("  2. In your terminal, run:")
    print('     $env:ALLOW_MONEY_TESTS="1"')
    print("     py -m resell resume")
    print("=" * 70)


def handle_buy_test(args: argparse.Namespace) -> int:
    """CLI handler for the buy-test command."""
    try:
        asyncio.run(
            run_buy_test(
                variant_id=args.variant_id,
                quantity=args.quantity,
                email=args.email,
            )
        )
        return 0
    except Exception as exc:
        print(f"\n[ERROR] Buy-test failed: {exc}", file=sys.stderr)
        return 1


async def run_resume() -> None:
    """Resume all pending/incomplete orders from the database."""
    settings = load_settings()
    mapping = load_mapping(settings.MAPPING_FILE)

    db = Database(settings.SQLITE_PATH)
    db.apply_migrations()
    repo = OrdersRepo(db)
    supplier = SupplierScraper()
    payment = ElectrumPaymentAdapter(
        rpc_url=settings.ELECTRUM_RPC_URL,
        rpc_user=settings.ELECTRUM_RPC_USER,
        rpc_password=settings.ELECTRUM_RPC_PASSWORD,
    )
    delivery = LateDeliveryAdapter(
        api_key=settings.SELLAUTH_API_KEY.get_secret_value(),
        shop_id=settings.SELLAUTH_SHOP_ID,
    )

    runner = JobRunner(
        repo=repo,
        supplier=supplier,
        payment=payment,
        delivery=delivery,
        mapping=mapping,
        settings=settings,
    )

    resumed = await runner.resume()
    resumed_list = resumed if resumed is not None else []
    order_ids = [o.id for o in resumed_list]
    print(
        f"Resumed {len(resumed_list)} active order(s) from database: {order_ids}",
        flush=True,
    )

    if resumed_list:
        print("Processing resumed orders...", flush=True)
        await runner.process_all_pending(timeout_seconds=600.0)
        print("All pending resumed orders processed.", flush=True)


def handle_resume(args: argparse.Namespace) -> int:
    """CLI handler for the resume command."""
    try:
        asyncio.run(run_resume())
        return 0
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\n[ERROR] Resume failed: {exc}", file=sys.stderr, flush=True)
        return 1


async def run_killswitch(resume: bool = False) -> None:
    """Engage emergency killswitch: zero store stocks, pause jobs, and notify Discord."""
    settings = load_settings()
    mapping = load_mapping(settings.MAPPING_FILE)
    alerts = DiscordAlerts(settings)

    if resume:
        deactivate_killswitch(settings)
        print(
            "\n[KILLSWITCH DISENGAGED] Lock file removed. "
            "Run 'py -m resell sync' to restore active stock."
        )
        try:
            await alerts.send_alert(
                title="Killswitch Disengaged",
                message="Killswitch lock was disengaged via CLI. Normal operation resumed.",
                is_critical=False,
            )
        except Exception:
            pass
        return

    # 1. Zero all mapped variants stock on SellAuth
    store_client = SellAuthStoreClient(
        api_key=settings.SELLAUTH_API_KEY.get_secret_value(),
        shop_id=settings.SELLAUTH_SHOP_ID,
    )
    print("\n[KILLSWITCH] Zeroing all store stocks on SellAuth...")
    for v in mapping.variants:
        try:
            await store_client.update_stock(
                product_id=v.our_product_id,
                variant_id=v.our_variant_id,
                stock=0,
            )
            print(f"  - Set variant '{v.name}' (#{v.our_variant_id}) stock to 0.")
        except Exception as exc:
            print(
                f"  [ERROR] Failed to set variant #{v.our_variant_id} stock to 0: {exc}",
                file=sys.stderr,
            )

    # 2. Engage persistent killswitch lock file
    activate_killswitch(settings, reason="CLI killswitch command executed")

    # 3. Dispatch critical red alert to DISCORD_WEBHOOK_ALERTS pinging admin role
    print("[KILLSWITCH] Dispatching critical red alert to Discord...")
    try:
        await alerts.send_alert(
            title="EMERGENCY KILLSWITCH ACTIVATED",
            message=(
                "Emergency killswitch was engaged via CLI. "
                "All storefront variant stocks have been set to 0. "
                "JobRunner order fulfillment has been PAUSED."
            ),
            is_critical=True,
        )
    except Exception as alert_err:
        logger.warning("Could not send killswitch alert to Discord: %s", alert_err)

    print("\n" + "=" * 70)
    print("[KILLSWITCH ENGAGED]")
    print("All storefront stocks set to 0.")
    print("Job runner order fulfillment paused.")
    print("Critical alert dispatched to Discord.")
    print("To disengage: py -m resell killswitch --resume")
    print("=" * 70)


def handle_killswitch(args: argparse.Namespace) -> int:
    """CLI handler for killswitch command."""
    try:
        asyncio.run(run_killswitch(resume=getattr(args, "resume", False)))
        return 0
    except Exception as exc:
        print(f"\n[ERROR] Killswitch execution failed: {exc}", file=sys.stderr)
        return 1


async def run_report(hours: int = 24) -> tuple[int, Decimal, Decimal, Decimal]:
    """Calculate and dispatch daily revenue, cost, and net profit report."""
    from datetime import UTC, datetime, timedelta

    settings = load_settings()
    mapping = load_mapping(settings.MAPPING_FILE)
    alerts = DiscordAlerts(settings)

    db = Database(settings.SQLITE_PATH)
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()

    total_orders = 0
    total_revenue = Decimal("0.00")
    total_cost = Decimal("0.00")

    repo = OrdersRepo(db)
    with db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM orders WHERE created_at >= ? ORDER BY id ASC;",
            (cutoff,),
        )
        orders_rows = cursor.fetchall()
        cursor.close()

        for row in orders_rows:
            order = repo._row_to_order(row)
            total_orders += 1
            if order.state == OrderState.DELIVERED:
                total_revenue += order.price_paid_usd

                purchase = repo.get_purchase(order.id)
                var_map = mapping.get_variant(order.variant_id)
                if var_map:
                    total_cost += var_map.max_price_usd * order.quantity
                elif purchase and purchase.ltc_amount_sats:
                    # Fallback estimate: 1 LTC ~= $100 USD (1 sat = 0.000001 USD)
                    est_usd = Decimal(purchase.ltc_amount_sats) * Decimal("0.000001")
                    total_cost += est_usd

    total_profit = max(Decimal("0.00"), total_revenue - total_cost)

    print("\n" + "=" * 60)
    print(f"DAILY REVENUE & PROFIT REPORT (Last {hours}h)")
    print("=" * 60)
    print(f"Total Orders:              {total_orders}")
    print(f"Total Revenue (USD):       ${total_revenue:.2f}")
    print(f"Total Supplier Cost (USD): ${total_cost:.2f}")
    print(f"Total Profit (USD):        ${total_profit:.2f}")
    print("=" * 60)

    try:
        await alerts.send_daily_report(
            total_orders=total_orders,
            total_revenue_usd=total_revenue,
            total_supplier_cost_usd=total_cost,
            total_profit_usd=total_profit,
        )
        print("Daily report sent to DISCORD_WEBHOOK_DELIVERED.")
    except Exception as exc:
        logger.warning("Failed to dispatch report to Discord: %s", exc)

    return total_orders, total_revenue, total_cost, total_profit


def handle_report(args: argparse.Namespace) -> int:
    """CLI handler for report command."""
    try:
        asyncio.run(run_report(hours=getattr(args, "hours", 24)))
        return 0
    except Exception as exc:
        print(f"\n[ERROR] Report generation failed: {exc}", file=sys.stderr)
        return 1


def handle_inspect(args: argparse.Namespace) -> int:
    """CLI handler for the inspect command."""
    try:
        output = asyncio.run(run_inspect(args.url))
        print(output)
        return 0
    except (SupplierUnavailableError, SupplierParseError) as exc:
        print(f"\n[ERROR] Supplier inspection failed: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"\n[ERROR] Invalid arguments: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[ERROR] Unexpected error: {exc}", file=sys.stderr)
        return 1


def handle_stub(cmd_name: str, stage: int) -> int:
    """Handler for commands scheduled in future stages."""
    print(f"Command '{cmd_name}' is scheduled for implementation in Stage {stage}.")
    return 1


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to appropriate handler."""
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="resell",
        description="ResellAuth: Automated SellAuth store reselling service.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a supplier product page")
    inspect_parser.add_argument("url", help="Supplier product page URL")

    # sync (Stage 6)
    subparsers.add_parser("sync", help="Run one-off stock sync (Stage 6)")

    # pay-test (Stage 7)
    pay_test_parser = subparsers.add_parser(
        "pay-test",
        help="Test tiny payment from Electrum-LTC (Stage 7)",
    )
    pay_test_parser.add_argument(
        "--address",
        help="Destination LTC address for tiny payment (optional)",
    )
    pay_test_parser.add_argument(
        "--sats",
        type=int,
        default=50000,
        help="Amount in satoshis to send (default: 50000 = 0.0005 LTC)",
    )

    # buy-test (Stage 8)
    buy_test_parser = subparsers.add_parser(
        "buy-test", help="Test purchase from supplier (Stage 8)"
    )
    buy_test_parser.add_argument(
        "--variant-id",
        type=str,
        default=None,
        help="Store variant ID or supplier variant ID to test",
    )
    buy_test_parser.add_argument(
        "--quantity",
        type=int,
        default=1,
        help="Quantity to purchase (default: 1)",
    )
    buy_test_parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Real email for checkout (SellAuth blocks example.com; defaults to buyer@gmail.com)",
    )

    # killswitch (Stage 10)
    killswitch_parser = subparsers.add_parser(
        "killswitch",
        help="Zero all store stock and pause jobs (Stage 10)",
    )
    killswitch_parser.add_argument(
        "--resume",
        action="store_true",
        help="Disengage killswitch and permit job execution",
    )

    # report (Stage 10)
    report_parser = subparsers.add_parser(
        "report",
        help="Print daily revenue and reconciliation report (Stage 10)",
    )
    report_parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Report lookback window in hours (default: 24)",
    )

    # resume (Stage 11)
    subparsers.add_parser("resume", help="Resume pending jobs from database")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "inspect":
        sys.exit(handle_inspect(args))
    elif args.command == "sync":
        sys.exit(handle_sync(args))
    elif args.command == "pay-test":
        sys.exit(handle_pay_test(args))
    elif args.command == "buy-test":
        sys.exit(handle_buy_test(args))
    elif args.command == "killswitch":
        sys.exit(handle_killswitch(args))
    elif args.command == "report":
        sys.exit(handle_report(args))
    elif args.command == "resume":
        sys.exit(handle_resume(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

