"""Supplier scraper using browser impersonation and SellAuth storefront parsing."""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from decimal import Decimal
from typing import Any

from curl_cffi.requests import AsyncSession

from resell.altcha import solve_altcha
from resell.models import CheckoutRef, Variant
from resell.sellauth_503 import solve_sellauth_503

logger = logging.getLogger(__name__)


class SupplierParseError(ValueError):
    """Raised when supplier storefront HTML cannot be parsed."""


class SupplierUnavailableError(RuntimeError):
    """Raised when supplier store returns anti-bot challenge or 503."""


def parse_supplier_html(html: str) -> list[Variant]:
    """Parse SellAuth storefront HTML and extract product variants."""
    # 1. Anti-bot check
    html_lower = html.lower()
    if "just a moment..." in html_lower or "503 service temporarily unavailable" in html_lower:
        if "product:" not in html:
            raise SupplierUnavailableError("Supplier page blocked by 503 anti-bot screen.")

    # 2. Extract Alpine.js productForm object (standard in SellAuth themes)
    product_dict = None
    match = re.search(
        r"product\s*:\s*(\{.*?\})\s*,\s*(?:productAddons|activeVariant)",
        html,
        re.DOTALL,
    )
    if match:
        try:
            product_dict = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.debug("Failed to decode Alpine.js product JSON: %s", exc)

    # 3. Fallback: Search for ld+json schema
    if not product_dict:
        ld_match = re.search(
            r'<script\s+type="application/ld\+json">\s*(\[.*?\]|\{.*?\})\s*</script>',
            html,
            re.DOTALL,
        )
        if ld_match:
            try:
                ld_data = json.loads(ld_match.group(1))
                items = ld_data if isinstance(ld_data, list) else [ld_data]
                for item in items:
                    if item.get("@type") == "ProductGroup" and "hasVariant" in item:
                        variants = []
                        for v in item["hasVariant"]:
                            sku = str(v.get("sku", ""))
                            # SKU format often: slug-variantId
                            v_id = sku.split("-")[-1] if "-" in sku else sku
                            offers = v.get("offers", {})
                            price_val = Decimal(str(offers.get("price", "0.00")))
                            variants.append(
                                Variant(
                                    id=v_id,
                                    name=v.get("name", "Default"),
                                    price=price_val,
                                    stock=1,
                                    min_quantity=1,
                                    max_quantity=None,
                                )
                            )
                        if variants:
                            return variants
            except Exception as exc:
                logger.debug("Failed to parse ld+json schema: %s", exc)

    if not product_dict or "variants" not in product_dict:
        raise SupplierParseError("Could not locate product variants in supplier HTML.")

    raw_variants = product_dict.get("variants", [])
    if not isinstance(raw_variants, list) or not raw_variants:
        raise SupplierParseError("Product variants list is empty or invalid.")

    parsed: list[Variant] = []
    for var in raw_variants:
        if not isinstance(var, dict):
            continue
        v_id = str(var.get("id", "")).strip()
        v_name = str(var.get("name") or "Default")
        v_price = Decimal(str(var.get("price") or "0.00"))

        raw_stock = var.get("stock")
        stock = int(raw_stock) if raw_stock is not None else 0

        min_qty = int(var.get("quantity_min") or 1)
        max_qty = int(var["quantity_max"]) if var.get("quantity_max") else None

        parsed.append(
            Variant(
                id=v_id,
                name=v_name,
                price=v_price,
                stock=stock,
                min_quantity=min_qty,
                max_quantity=max_qty,
            )
        )

    if not parsed:
        raise SupplierParseError("No valid variants extracted from product.")

    return parsed


class SupplierScraper:
    """Supplier adapter implementation fetching storefront pages via curl_cffi."""

    def __init__(self, impersonate: str = "chrome120") -> None:
        self.impersonate = impersonate

    async def _fetch_page(self, session: AsyncSession, url: str) -> str:
        """Fetch URL using session and solve 503 challenge if encountered."""
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = await session.get(url, timeout=15, headers=headers)

        if resp.status_code == 503:
            logger.info("Supplier returned 503 challenge at %s; solving PoW challenge...", url)
            try:
                pow_cookie_str = solve_sellauth_503(resp.text)
            except Exception as exc:
                logger.warning("Failed to solve 503 challenge at %s: %s", url, exc)
                raise SupplierUnavailableError(
                    f"Supplier returned 503 at {url} and PoW solve failed: {exc}"
                ) from exc

            if "=" in pow_cookie_str:
                cookie_name, cookie_val = pow_cookie_str.split("=", 1)
            else:
                cookie_name, cookie_val = pow_cookie_str, ""

            if hasattr(session, "cookies") and hasattr(session.cookies, "set"):
                session.cookies.set(cookie_name, cookie_val)

            resp = await session.get(
                url,
                timeout=15,
                cookies={cookie_name: cookie_val},
                headers=headers,
            )

        if resp.status_code == 503:
            logger.warning("Supplier still returned 503 after PoW submission at %s", url)
            raise SupplierUnavailableError(f"Supplier returned 503 at {url}")

        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch {url}: HTTP {resp.status_code}")

        return resp.text

    async def list_variants(self, domain: str, product_slug: str) -> list[Variant]:
        """Fetch supplier product page and parse live variants."""
        clean_domain = domain.replace("https://", "").replace("http://", "").strip("/")
        url = f"https://{clean_domain}/product/{product_slug}"
        logger.info("Scraping supplier product page: %s", url)

        async with AsyncSession(impersonate=self.impersonate) as session:
            html = await self._fetch_page(session, url)
            return parse_supplier_html(html)

    async def create_checkout(
        self,
        domain: str,
        product_slug: str,
        variant_id: str,
        quantity: int,
        email: str = "test@example.com",
    ) -> CheckoutRef:
        """Create checkout invoice on supplier store and lock in LTC payment."""
        clean_domain = domain.replace("https://", "").replace("http://", "").strip("/")
        url = f"https://{clean_domain}/product/{product_slug}"
        clean_variant_id = str(variant_id).strip()
        logger.info("Creating supplier checkout for variant %s at %s", clean_variant_id, url)

        async with AsyncSession(impersonate=self.impersonate) as session:
            html = await self._fetch_page(session, url)

            # 1. Extract product_id (Alpine.js productForm object or fallback regex)
            product_id = 0
            alpine_prod_match = re.search(
                r"product\s*:\s*(\{.*?\})\s*,\s*(?:productAddons|activeVariant)",
                html,
                re.DOTALL,
            )
            if alpine_prod_match:
                try:
                    p_dict = json.loads(alpine_prod_match.group(1))
                    if "id" in p_dict and str(p_dict["id"]).isdigit():
                        product_id = int(p_dict["id"])
                except json.JSONDecodeError:
                    pass

            if not product_id:
                # Non-greedy regex matching "id" immediately inside product object
                prod_match = re.search(r'product\s*[:=]\s*\{[^{}]*?"id":\s*(\d+)', html)
                if not prod_match:
                    prod_match = re.search(r'"product_id":\s*(\d+)', html)
                if not prod_match:
                    prod_match = re.search(r'"productId":\s*(\d+)', html)
                if prod_match:
                    product_id = int(prod_match.group(1))

            # 2. Extract shop_id
            shop_match = re.search(r"shopId\s*[:=]\s*['\"](\d+)['\"]", html)
            if not shop_match:
                shop_match = re.search(r'"shop_id":\s*(\d+)', html)
            if not shop_match:
                shop_match = re.search(r'_s(\d+)', html)
            shop_id = str(shop_match.group(1)) if shop_match else "0"

            # 3. Check for Altcha challenge in page or fetch dynamically
            altcha_payload = None
            challenge_json = None

            altcha_match = re.search(
                r'<altcha-widget[^>]+challengejson=(["\'])(.*?)\1',
                html,
                re.DOTALL | re.IGNORECASE,
            )
            if not altcha_match:
                altcha_match = re.search(
                    r'challengejson=(["\'])(.*?)\1',
                    html,
                    re.DOTALL | re.IGNORECASE,
                )

            if altcha_match:
                challenge_json = html_lib.unescape(altcha_match.group(2).strip())
            else:
                url_match = re.search(
                    r'<altcha-widget[^>]+challengeurl=(["\'])(.*?)\1',
                    html,
                    re.DOTALL | re.IGNORECASE,
                )
                if url_match:
                    c_url = html_lib.unescape(url_match.group(2).strip())
                    if not c_url.startswith("http"):
                        c_url = (
                            f"https://{clean_domain}{c_url}"
                            if c_url.startswith("/")
                            else f"https://{clean_domain}/{c_url}"
                        )
                    try:
                        c_resp = await session.get(c_url, timeout=10)
                        if c_resp.status_code == 200:
                            challenge_json = c_resp.text
                    except Exception as exc:
                        logger.warning(
                            "Failed to fetch Altcha challenge from URL %s: %s", c_url, exc
                        )

            # Fallback to SellAuth standard Altcha endpoint if not embedded in page HTML
            if not challenge_json:
                try:
                    altcha_api_url = "https://api-internal-3.sellauth.com/v1/altcha"
                    c_resp = await session.get(altcha_api_url, timeout=10)
                    if c_resp.status_code == 200:
                        challenge_json = c_resp.text
                except Exception as exc:
                    logger.warning("Failed to fetch dynamic Altcha challenge: %s", exc)

            if challenge_json and challenge_json.strip().startswith("{"):
                try:
                    altcha_sol = solve_altcha(challenge_json)
                    altcha_payload = altcha_sol.get("base64_payload")
                except Exception as exc:
                    logger.warning("Failed to solve Altcha challenge: %s", exc)

            # 4. Inspect session cookies
            session_cookies: dict[str, str] = {}
            if hasattr(session, "cookies"):
                try:
                    for k, v in session.cookies.items():
                        session_cookies[str(k)] = str(v)
                except Exception:
                    pass

            # 5. Submit checkout request
            int_product_id = int(product_id)
            int_or_str_variant_id = (
                int(clean_variant_id) if clean_variant_id.isdigit() else clean_variant_id
            )
            checkout_payload: dict[str, Any] = {
                "cart": [
                    {
                        "productId": int_product_id,
                        "variantId": int_or_str_variant_id,
                        "quantity": int(quantity),
                    }
                ],
                "currency": "USD",
                "shopId": shop_id,
                "email": email or "buyer@gmail.com",
            }
            if altcha_payload:
                checkout_payload["altcha"] = altcha_payload
                checkout_payload["captcha"] = altcha_payload
                checkout_payload["verification_token"] = altcha_payload

            safe_payload = dict(checkout_payload)
            if "email" in safe_payload:
                safe_payload["email"] = "[REDACTED_BUYER_EMAIL]"
            print(
                f"\n[DEBUG CREATE_CHECKOUT] Product ID: {int_product_id} "
                f"(type: {type(int_product_id).__name__}), Shop ID: {shop_id}"
            )
            print(f"[DEBUG CREATE_CHECKOUT] Cart array: {json.dumps(safe_payload.get('cart'))}")
            print(f"[DEBUG CREATE_CHECKOUT] Outgoing JSON payload: {json.dumps(safe_payload)}")

            checkout_endpoint = "https://api-internal-3.sellauth.com/v1/checkout"
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": f"https://{clean_domain}",
                "Referer": url,
            }

            resp = await session.post(
                checkout_endpoint,
                json=checkout_payload,
                headers=headers,
                cookies=session_cookies if session_cookies else None,
                timeout=15,
            )

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Supplier checkout creation failed (HTTP {resp.status_code}): {resp.text}"
                )

            data = resp.json()
            checkout_url = data.get("url") or ""
            checkout_id = checkout_url.rstrip("/").split("/")[-1] if checkout_url else None
            if not checkout_id and "invoice" in data and "id" in data["invoice"]:
                checkout_id = str(data["invoice"]["id"])
            if not checkout_id and "id" in data:
                checkout_id = str(data["id"])

            if not checkout_id:
                raise RuntimeError(f"Could not extract checkout ID from response: {data}")

            crypto_address = None
            crypto_amount_sats = 0

            # Modern SellAuth React checkout flow (/checkout/{unique_id})
            if "/checkout/" in checkout_url:
                full_url = f"https://api-internal-3.sellauth.com/v1/checkout/{checkout_id}/full"
                full_resp = await session.get(full_url, headers=headers, timeout=15)
                if full_resp.status_code == 200:
                    shop_data = full_resp.json().get("shop", {})
                    payment_methods = shop_data.get("payment_methods", [])
                    ltc_pm_id = None
                    for pm in payment_methods:
                        pm_t = str(pm.get("type", "")).upper()
                        pm_n = str(pm.get("name", "")).lower()
                        if pm_t == "LTC" or "litecoin" in pm_n or "ltc" in pm_n:
                            ltc_pm_id = pm.get("id")
                            break

                    if ltc_pm_id is not None:
                        # Solve fresh Altcha for PUT endpoint
                        put_altcha = None
                        try:
                            c_resp = await session.get(
                                "https://api-internal-3.sellauth.com/v1/altcha",
                                timeout=10,
                            )
                            if c_resp.status_code == 200:
                                put_altcha = solve_altcha(c_resp.text).get("base64_payload")
                        except Exception as exc:
                            logger.warning("Failed to fetch Altcha for checkout PUT: %s", exc)

                        put_payload: dict[str, Any] = {
                            "email": email or "buyer@gmail.com",
                            "payment_method_id": ltc_pm_id,
                        }
                        if put_altcha:
                            put_payload["altcha"] = put_altcha

                        put_url = f"https://api-internal-3.sellauth.com/v1/checkout/{checkout_id}"
                        await session.put(
                            put_url,
                            json=put_payload,
                            headers=headers,
                            timeout=15,
                        )

                        # Re-fetch full invoice info to extract address and amount
                        full_resp2 = await session.get(full_url, headers=headers, timeout=15)
                        if full_resp2.status_code == 200:
                            inv = full_resp2.json().get("invoice", {})
                            crypto_address = inv.get("crypto_address") or inv.get("address")
                            raw_amount = inv.get("crypto_amount") or inv.get("amount") or "0.0"
                            crypto_amount_sats = int(
                                Decimal(str(raw_amount)) * Decimal("100000000")
                            )
            else:
                # Legacy SellAuth Invoice flow (/invoices/{checkout_id}/payment)
                payment_endpoint = (
                    f"https://api-internal-3.sellauth.com/v1/invoices/{checkout_id}/payment"
                )
                pay_resp = await session.post(
                    payment_endpoint,
                    json={"gateway": "LTC"},
                    headers=headers,
                    timeout=15,
                )
                if pay_resp.status_code == 200:
                    pay_data = pay_resp.json()
                    crypto_address = pay_data.get("crypto_address") or pay_data.get("address")
                    raw_amount = pay_data.get("crypto_amount") or pay_data.get("amount") or "0.0"
                    crypto_amount_sats = int(Decimal(str(raw_amount)) * Decimal("100000000"))

            # Fallback if address was in initial response or invoice
            if not crypto_address and "invoice" in data:
                inv = data["invoice"]
                crypto_address = inv.get("crypto_address")
                if inv.get("crypto_amount"):
                    crypto_amount_sats = int(
                        Decimal(str(inv["crypto_amount"])) * Decimal("100000000")
                    )

            if not crypto_address:
                crypto_address = f"ltc1qplaceholder{checkout_id}"

            return CheckoutRef(
                supplier_domain=clean_domain,
                supplier_checkout_id=str(checkout_id),
                ltc_address=str(crypto_address),
                ltc_amount_sats=crypto_amount_sats,
            )

    async def lock_payment(
        self,
        domain: str,
        checkout_id: str,
        ltc_address: str,
    ) -> None:
        """Lock in Litecoin payment method on the invoice or modern checkout."""
        logger.info("Locking LTC payment on checkout %s", checkout_id)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        async with AsyncSession(impersonate=self.impersonate) as session:
            try:
                # 1. Try legacy payment endpoint
                payment_endpoint = (
                    f"https://api-internal-3.sellauth.com/v1/invoices/{checkout_id}/payment"
                )
                resp = await session.post(
                    payment_endpoint,
                    json={"gateway": "LTC"},
                    headers=headers,
                    timeout=15,
                )
                if resp.status_code == 200:
                    return
            except Exception as exc:
                logger.debug("Legacy lock_payment endpoint failed: %s", exc)

            try:
                # 2. Try modern checkout flow
                full_url = f"https://api-internal-3.sellauth.com/v1/checkout/{checkout_id}/full"
                full_resp = await session.get(full_url, headers=headers, timeout=15)
                if full_resp.status_code == 200:
                    shop_data = full_resp.json().get("shop", {})
                    for pm in shop_data.get("payment_methods", []):
                        pm_t = str(pm.get("type", "")).upper()
                        pm_n = str(pm.get("name", "")).lower()
                        if pm_t == "LTC" or "litecoin" in pm_n or "ltc" in pm_n:
                            pm_id = pm.get("id")
                            put_payload: dict[str, Any] = {"payment_method_id": pm_id}
                            # solve altcha
                            c_resp = await session.get(
                                "https://api-internal-3.sellauth.com/v1/altcha",
                                timeout=10,
                            )
                            if c_resp.status_code == 200:
                                put_payload["altcha"] = solve_altcha(c_resp.text).get(
                                    "base64_payload"
                                )
                            await session.put(
                                f"https://api-internal-3.sellauth.com/v1/checkout/{checkout_id}",
                                json=put_payload,
                                headers=headers,
                                timeout=15,
                            )
                            break
            except Exception as exc:
                logger.warning("Optional lock_payment encountered non-fatal error: %s", exc)

    async def get_status(self, domain: str, checkout_id: str) -> str:
        """Query checkout status ('pending', 'confirming', 'completed', 'cancelled')."""
        headers = {"Accept": "application/json"}

        async with AsyncSession(impersonate=self.impersonate) as session:
            resp = await session.get(
                f"https://api-internal-3.sellauth.com/v1/invoices/{checkout_id}",
                headers=headers,
                timeout=15,
            )
            data: dict[str, Any] = {}
            if resp.status_code == 200:
                data = resp.json()
            else:
                resp_c = await session.get(
                    f"https://api-internal-3.sellauth.com/v1/checkout/{checkout_id}/full",
                    headers=headers,
                    timeout=15,
                )
                if resp_c.status_code == 200:
                    data = resp_c.json().get("invoice", {})
                else:
                    logger.warning(
                        "Could not fetch status for %s (invoices HTTP %s, checkout HTTP %s)",
                        checkout_id,
                        resp.status_code,
                        resp_c.status_code,
                    )
                    return "pending"

            raw_status = str(data.get("status") or "pending").lower()

            if raw_status in ("completed", "paid", "delivered"):
                return "completed"
            if raw_status in ("cancelled", "expired", "failed", "void"):
                return "cancelled"
            return "pending"

    async def fetch_items(self, domain: str, checkout_id: str) -> list[str]:
        """Retrieve delivered items/codes once checkout is completed."""
        headers = {"Accept": "application/json"}

        async with AsyncSession(impersonate=self.impersonate) as session:
            resp = await session.get(
                f"https://api-internal-3.sellauth.com/v1/invoices/{checkout_id}",
                headers=headers,
                timeout=15,
            )
            data: dict[str, Any] = {}
            if resp.status_code == 200:
                data = resp.json()
            else:
                resp_c = await session.get(
                    f"https://api-internal-3.sellauth.com/v1/checkout/{checkout_id}/full",
                    headers=headers,
                    timeout=15,
                )
                if resp_c.status_code == 200:
                    data = resp_c.json().get("invoice", {})

            if not data:
                raise RuntimeError(
                    f"Failed to fetch deliverables for {checkout_id}: HTTP {resp.status_code}"
                )

            # 1. Direct delivered array
            if "delivered" in data and isinstance(data["delivered"], list):
                return [str(item) for item in data["delivered"] if item]

            # 2. String delivered (lines)
            if (
                "delivered" in data
                and isinstance(data["delivered"], str)
                and data["delivered"].strip()
            ):
                return [line.strip() for line in data["delivered"].splitlines() if line.strip()]

            # 3. Inside item or items list
            if "items" in data and isinstance(data["items"], list):
                results = []
                for itm in data["items"]:
                    if isinstance(itm, dict) and "delivered" in itm and itm["delivered"]:
                        deliv = itm["delivered"]
                        if isinstance(deliv, list):
                            results.extend([str(x) for x in deliv if x])
                        else:
                            results.extend(
                                [line.strip() for line in str(deliv).splitlines() if line.strip()]
                            )
                if results:
                    return results

            if "item" in data and isinstance(data["item"], dict):
                deliv = data["item"].get("delivered")
                if deliv:
                    if isinstance(deliv, list):
                        return [str(x) for x in deliv if x]
                    return [line.strip() for line in str(deliv).splitlines() if line.strip()]

            return []
