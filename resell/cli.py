"""Command-line interface for resell-sellauth."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import urllib.parse

from resell.adapters.supplier_scrape import (
    SupplierParseError,
    SupplierScraper,
    SupplierUnavailableError,
)
from resell.models import Variant

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
    subparsers.add_parser("pay-test", help="Test tiny payment from Electrum-LTC (Stage 7)")

    # buy-test (Stage 8)
    subparsers.add_parser("buy-test", help="Test purchase from supplier (Stage 8)")

    # killswitch (Stage 10)
    subparsers.add_parser("killswitch", help="Zero all store stock and stop jobs (Stage 10)")

    # report (Stage 10)
    subparsers.add_parser("report", help="Print daily revenue and reconciliation report (Stage 10)")

    # resume (Stage 11)
    subparsers.add_parser("resume", help="Resume pending jobs from database (Stage 11)")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    stage_map = {
        "sync": 6,
        "pay-test": 7,
        "buy-test": 8,
        "killswitch": 10,
        "report": 10,
        "resume": 11,
    }

    if args.command == "inspect":
        sys.exit(handle_inspect(args))
    elif args.command in stage_map:
        sys.exit(handle_stub(args.command, stage_map[args.command]))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

