"""Read-only supplier product page probe for Stage 0 Discovery.

Fetches a supplier's product page (or reads a saved HTML file) and extracts
variant IDs, titles, prices, live stock, and min/max quantity limits.

Usage:
    python scratch/probe_supplier.py https://supplier.sellauth.com/product/slug
    python scratch/probe_supplier.py tests/fixtures/supplier_pages/sample.html
    python scratch/probe_supplier.py https://supplier.sellauth.com/product/slug --save output.html
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def fetch_url(url: str, user_agent: str = DEFAULT_USER_AGENT) -> str:
    """Fetch HTML content from a URL using standard urllib with browser headers."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_bytes: bytes = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return content_bytes.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        print(f"[!] HTTP Error {exc.code}: {exc.reason}", file=sys.stderr)
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            detect_challenges(err_body)
        except Exception:
            pass
        raise
    except urllib.error.URLError as exc:
        print(f"[!] Network Error: {exc.reason}", file=sys.stderr)
        raise


def detect_challenges(html: str) -> list[str]:
    """Check for anti-bot or challenge screens."""
    indicators = []
    if "altcha" in html.lower():
        indicators.append("Altcha challenge detected")
    if "cf-chl" in html or "cloudflare" in html.lower() and "turnstile" in html.lower():
        indicators.append("Cloudflare Turnstile challenge detected")
    if "just a moment..." in html.lower():
        indicators.append("Cloudflare waiting room / 503 challenge detected")
    if "503 service temporarily unavailable" in html.lower():
        indicators.append("503 Service Unavailable challenge detected")
    return indicators


def extract_next_data(html: str) -> dict[str, Any] | None:
    """Extract and parse __NEXT_DATA__ JSON script tag from HTML."""
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"\s+type="application/json">\s*(.*?)\s*</script>',
        html,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        print(f"[!] Warning: Failed to parse __NEXT_DATA__ JSON: {exc}", file=sys.stderr)
        return None


def find_product_data(data: dict[str, Any]) -> dict[str, Any] | None:
    """Traverse Next.js data tree to locate product details."""
    props = data.get("props", {})
    page_props = props.get("pageProps", {})

    # Common shapes on SellAuth storefronts
    if "product" in page_props:
        return page_props["product"]
    if "item" in page_props:
        return page_props["item"]

    # Search recursively for key containing "variants"
    def search_dict(node: Any) -> dict[str, Any] | None:
        if isinstance(node, dict):
            if "variants" in node and ("name" in node or "title" in node or "id" in node):
                return node
            for val in node.values():
                res = search_dict(val)
                if res is not None:
                    return res
        elif isinstance(node, list):
            for item in node:
                res = search_dict(item)
                if res is not None:
                    return res
        return None

    return search_dict(page_props)


def probe(html: str, source_label: str) -> None:
    """Parse product HTML and print diagnostic variant/stock report."""
    print("=" * 70)
    print(f"PROBE REPORT FOR: {source_label}")
    print("=" * 70)

    # 1. Challenge check
    challenges = detect_challenges(html)
    if challenges:
        print("[!] NOTICE: Protection / Challenges detected:")
        for ch in challenges:
            print(f"    - {ch}")
    else:
        print("[+] Anti-bot check: No active challenge screens detected.")

    # 2. Extract structured data
    next_data = extract_next_data(html)
    if not next_data:
        print("[-] Could not find `<script id=\"__NEXT_DATA__\">` in HTML.")
        print("    Checking for embedded JSON blobs or raw scripts...")
        raw_variants = re.findall(r'"variants"\s*:\s*(\[[^\]]+\])', html)
        if raw_variants:
            print(f"[+] Found {len(raw_variants)} candidate variants blob(s) in HTML.")
        else:
            print("[-] No variants found via regex fallback.")
        return

    print("[+] Found __NEXT_DATA__ script tag.")
    product = find_product_data(next_data)
    if not product:
        print("[-] Could not locate product object in pageProps.")
        page_props_keys = list(next_data.get("props", {}).get("pageProps", {}).keys())
        print(f"    Available pageProps keys: {page_props_keys}")
        return

    # 3. Print product info
    prod_id = product.get("id") or product.get("_id") or "N/A"
    prod_name = product.get("name") or product.get("title") or "Unnamed"
    currency = product.get("currency") or "USD"
    print(f"Product: {prod_name} (ID: {prod_id})")
    print(f"Currency: {currency}")
    print("-" * 70)

    # 4. Extract variants
    variants = product.get("variants", [])
    if not isinstance(variants, list):
        print(f"[-] 'variants' is not a list: {type(variants)}")
        return

    print(f"Variants Found: {len(variants)}\n")
    headers = (
        f"{'Variant ID':<12} | {'Name':<24} | {'Price':<10} | "
        f"{'Stock':<8} | {'Min Qty':<8} | {'Max Qty':<8}"
    )
    print(headers)
    print("-" * len(headers))

    for var in variants:
        if not isinstance(var, dict):
            continue
        v_id = str(var.get("id", "N/A"))
        v_name = str(var.get("name") or var.get("title") or "Default")[:24]
        v_price = str(var.get("price") or var.get("amount") or "N/A")
        v_stock = var.get("stock")
        stock_str = "Unlimited (-1)" if v_stock == -1 else str(v_stock)
        min_qty = str(var.get("min_quantity") or var.get("min_qty") or 1)
        max_qty = str(var.get("max_quantity") or var.get("max_qty") or "N/A")

        row = (
            f"{v_id:<12} | {v_name:<24} | {v_price:<10} | "
            f"{stock_str:<8} | {min_qty:<8} | {max_qty:<8}"
        )
        print(row)

    print("=" * 70)


def main() -> None:
    """CLI Entrypoint."""
    parser = argparse.ArgumentParser(
        description="Probe supplier page for variants, stock, and limits."
    )
    parser.add_argument("target", help="Supplier product URL or local HTML file path")
    parser.add_argument("--save", help="Optional path to save raw fetched HTML for fixtures")
    args = parser.parse_args()

    target_path = Path(args.target)
    if target_path.is_file():
        print(f"Reading from local file: {target_path}")
        html = target_path.read_text(encoding="utf-8", errors="replace")
    else:
        print(f"Fetching remote URL: {args.target}")
        html = fetch_url(args.target)

    if args.save:
        save_path = Path(args.save)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(html, encoding="utf-8")
        print(f"[+] Saved raw HTML to: {save_path}")

    probe(html, args.target)


if __name__ == "__main__":
    main()

