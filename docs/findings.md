# Stage 0 Findings

Recorded on 2026-11-16 by Human.

## Delivery mode [confirmed from docs]

SellAuth HTTP notifications: connect timeout 5s, request timeout 10s,
3 attempts total, 5s apart.
Held-open delivery for LTC waits is impossible.
Delivery mode: LATE ONLY via POST /v1/shops/{shopId}/invoices/{invoiceId}/deliver.
Source: https://sellauth.mintlify.site/developers/http-notifications

## Webhook signature [confirmed from docs]

HMAC-SHA256 over the raw request body. Header: X-Signature.
Idempotency-Key header present. Use it for dedupe.
Source: same page.

## Stock update [confirmed from docs]

PUT /v1/shops/{shopId}/products/{productId}/stock/{variantId}
Body: {"stock": N}. -1 means unlimited, not zero.
Source: https://sellauth.mintlify.site/api-reference/products/update-stock

## Late delivery [confirmed from docs]

POST /v1/shops/{shopId}/invoices/{invoiceId}/deliver
Delivers items and notifies the customer.
Source: https://sellauth.mintlify.site/api-reference/invoices/deliver-invoice-items

## Replace delivered items [confirmed from API]

POST /v1/shops/{shopId}/invoices/{invoiceId}/replace-delivered
Body: {"invoice_item_id": int, "replacements": list[str]}
Overwrites previously delivered items (e.g. placeholder text from webhook response) with real deliverables and notifies the customer.
Source: https://sellauth.mintlify.site/api-reference/invoices/replace-delivered-items

## Refund endpoint

Exists? [Pending SellAuth Support Reply] Link: \***\*\_\_\_\_\*\***
Source: \***\*\_\_\_\_\*\***

## Real webhook payload (redacted)

```json
{
  "event": "INVOICE.ITEM.DELIVER-DYNAMIC",
  "delivery_reason": "purchase",
  "subscription": null,
  "id": 17506338,
  "coupon_id": null,
  "shipping_fee": "0.00",
  "paypalff_email": null,
  "shop_customer_id": 4684602,
  "created_at": "2026-11-16T16:44:30.000000Z",
  "updated_at": "2026-11-16T16:46:19.000000Z",
  "completed_at": "2026-11-16T16:46:19.000000Z",
  "processed_email_sent_at": null,
  "archived_at": null,
  "salt": "2c30289477173",
  "gateway": "LTC",
  "payment_method_id": 242695,
  "external_url": null,
  "sumup_checkout_id": null,
  "email": "buyer@example.com",
  "delivered": null,
  "redirect_url": null,
  "status": "completed",
  "fulfillment_status": null,
  "status_description": null,
  "price": "0.00",
  "tax_rate": "0.00",
  "currency": "USD",
  "customer_currency": "INR",
  "price_usd": "0.00",
  "balance_applied": null,
  "balance_applied_usd": null,
  "paid": "0.00",
  "paid_usd": "0.00",
  "custom_fields": null,
  "dashboard_note": null,
  "ip": "157.50.247.40",
  "country_code": "IN",
  "user_agent": "Mozilla\/5.0 (Windows NT 10.0; Win64; x64; rv:160.0) Gecko\/20100101 Firefox\/160.0",
  "asn": "55836",
  "utm_source": null,
  "utm_medium": null,
  "utm_campaign": null,
  "utm_term": null,
  "utm_content": null,
  "referrer_host": null,
  "crypto_address": null,
  "crypto_amount": null,
  "shop_id": 226299,
  "cashapp_cashtag": null,
  "cashapp_note": null,
  "cashapp_email": null,
  "venmo_tag": null,
  "venmo_email": null,
  "venmo_note": null,
  "zelle_recipient": null,
  "zelle_name": null,
  "zelle_email": null,
  "zelle_note": null,
  "skrillp2p_email": null,
  "skrillp2p_note": null,
  "wise_tag": null,
  "wise_email": null,
  "wise_note": null,
  "manual": false,
  "paypalff_currency": "USD",
  "paypalff_price": null,
  "paypalff_note": null,
  "customer_id": null,
  "transaction_id": null,
  "proof_of_payment": null,
  "proof_of_payment_image": null,
  "metadata": null,
  "source": "storefront",
  "billing_name": null,
  "billing_address_line1": null,
  "billing_address_line2": null,
  "billing_city": null,
  "billing_state": null,
  "billing_postcode": null,
  "billing_country": null,
  "shipping_name": null,
  "shipping_address_line1": null,
  "shipping_address_line2": null,
  "shipping_city": null,
  "shipping_state": null,
  "shipping_postcode": null,
  "shipping_country": null,
  "shipping_phone": null,
  "tracking_code": null,
  "tracking_link": null,
  "shipped_at": null,
  "delivered_at": null,
  "fulfillment_note": null,
  "abandoned_dismissed_at": null,
  "abandoned_recovery_sent_at": null,
  "abandoned_recovery_due_at": null,
  "abandoned_recovery_skipped_at": null,
  "abandoned_recovery_skip_reason": null,
  "abandoned_recovery_source": null,
  "shipping_zone_id": null,
  "withdrawal_disabled": false,
  "affiliate_customer_id": null,
  "affiliate_code": null,
  "affiliate_commission_usd": null,
  "affiliate_commission_skip_reason": null,
  "affiliate_tier_id": null,
  "product_subscription_id": null,
  "subscription_cycle": null,
  "is_reseller_order": false,
  "reseller_tier_id": null,
  "reseller_source": null,
  "cashback_usd": null,
  "coupon_use": null,
  "expires_at": null,
  "unique_id": "2c30289477173-0000017506338",
  "customer": {
    "id": 4684602,
    "shop_id": 226299,
    "email": "buyer@example.com",
    "discord_id": null,
    "discord_username": null
  },
  "coupon": null,
  "payments": [],
  "item": {
    "id": 16132365,
    "invoice_id": 17506338,
    "product_id": 996063,
    "variant_id": 2092559,
    "custom_name": null,
    "status": "pending",
    "price": "1.00",
    "quantity": 2,
    "coupon_id": null,
    "coupon_discount": "0.00",
    "volume_discount": "2.00",
    "gateway_fee": "0.00",
    "tax_inclusive": true,
    "total_tax": "0.00",
    "total_price": "0.00",
    "total_price_usd": "0.00",
    "custom_fields": null,
    "delivered": null,
    "completed_at": null,
    "product": { "id": 996063, "name": "Test", "type": "variant" },
    "variant": { "id": 2092559, "name": "Default" }
  },
  "invoice_id": 17506338
}
```

Field names:

- invoice id: `invoice_id` (or root `id`)
- item id: `item.id`
- product id: `item.product_id`
- variant id: `item.variant_id`
- quantity: `item.quantity`
- email: `email` (or `customer.email`)
- price paid: `item.total_price_usd`
- currency: `currency`
- custom fields: `custom_fields` (at both root and inside `item`)
- idempotency header: `idempotency-key` (lowercase in headers)
- signature header: `x-signature`

## Test order outcomes (fast webhook)

| Wait before reply           | Delivered? | Retries? (count) | Buyer saw                     |
| --------------------------- | ---------- | ---------------- | ----------------------------- |
| 0 seconds (immediate reply) | Yes        | 0                | "stage0-placeholder-delivery" |

## Supplier: confirmation requirement

| Supplier           | Confirmations | Accepts before 1 conf? | Notes                          |
| ------------------ | ------------- | ---------------------- | ------------------------------ |
| vna.mysellauth.com | [Ask them]    | [Ask them]             | Approved for automated buying. |

## Supplier: quantity limits per variant

| Supplier           | Variant ID      | Min             | Max             | Notes                   |
| ------------------ | --------------- | --------------- | --------------- | ----------------------- |
| vna.mysellauth.com | [Pending parse] | [Pending parse] | [Pending parse] | See saved HTML fixture. |

## Supplier: written permission for automated purchasing

| Supplier           | Date       | Contact    | Link to saved reply |
| ------------------ | ---------- | ---------- | ------------------- |
| vna.mysellauth.com | 2026-11-16 | Discord DM | (Saved locally)     |

## Scraper Blocking & Anti-Bot Resolution

- The Stage 0 probe hit a `503 Service Unavailable`.
- SellAuth uses an obfuscated JavaScript Proof-of-Work challenge screen when 503 is returned:
  - Page embeds a rotated array `const a0_0x2a54 = [...]` and an IIFE shift `(a0_0x2a54, 0x...)`.
  - Reversing the shift reveals the dynamic cookie name (e.g. `yX3=`) and the challenge string.
  - Solution requires finding `i` where `sha1(challenge + i).digest()` has target bytes `(0xb0, 0x0b)` at offset `n1 = int(challenge[0], 16)`.
  - Resulting cookie string (e.g. `yX3=<challenge><i>`) is set on the HTTP session to bypass the 503 on retry.
- Implemented in `resell/sellauth_503.py` and dynamically applied in `resell/adapters/supplier_scrape.py`.

## SellAuth Checkout API (Stage 8)

- Endpoint: `POST https://api-internal-3.sellauth.com/v1/checkout`
- Validation: Field `email` strictly enforces deliverable email domain checks (fails with `HTTP 422 {"error":"The email must be a valid email address."}` if domain is `example.com` or other dummy domains). A real email address (e.g. Gmail/Outlook) is required by SellAuth checkout. Added `--email` flag to `buy-test`.
- Altcha Captcha Verification: SellAuth product pages embed `<altcha-widget challengejson='...'>` or `challengeurl`. If omitted or unsolved, checkout returns `HTTP 422 {"error":"captcha_verification_failed","message":"Captcha verification failed."}`. Solved token (`base64_payload`) must be sent in fields `altcha` and `verification_token` in the checkout JSON. Regex extraction must use backreference matching `(["\'])(.*?)\1` to avoid truncating on double-quotes inside single-quoted attribute values.
- Payment selection: `POST https://api-internal-3.sellauth.com/v1/invoices/{id}/payment` with `{"gateway": "LTC"}` locks the Litecoin gateway and returns `crypto_address` and `crypto_amount`.
- Modern SellAuth Storefronts (React SPA e.g. `dnclub.shop`):
  - Do not embed `<altcha-widget>` in initial HTML; challenge is fetched dynamically via `GET https://api-internal-3.sellauth.com/v1/altcha`.
  - Return `url: "https://<domain>/checkout/<unique_id>"` instead of `/invoice/<id>`.
  - Payment locking requires querying `GET https://api-internal-3.sellauth.com/v1/checkout/{unique_id}/full` to find the LTC payment method ID, solving a second Altcha challenge, and executing `PUT https://api-internal-3.sellauth.com/v1/checkout/{unique_id}` with `{"email": email, "payment_method_id": ltc_pm_id, "altcha": solved_token}`. Re-querying `/full` exposes the locked `crypto_address` and `crypto_amount`.
  - Variant IDs in themes may contain trailing whitespace (e.g. `"2069452  "`). Passing unstripped strings causes schema/integer conversion errors in backend cart validation; `.strip()` normalizes them to numeric IDs.
  - Greedy regex extraction on product JSON (`r'product\s*:\s*\{[^}]*"id":\s*(\d+)'`) skipped the actual product ID (`992462`) and matched the nested `category: {"id": 32295}`, resulting in SellAuth returning `HTTP 422 {"error":"Product #1 not found."}`. Parsing the full Alpine.js `productForm` JSON object guarantees accurate extraction of the top-level product ID (`992462`) as an explicit `int`.

## Stage 9 Findings: Webhook Deliverables, Auto-Migration, and Supplier Privacy

- SellAuth HTTP notification responses serve as the permanent deliverable for invoices:
  - If the webhook endpoint responds with raw JSON, SellAuth captures the raw JSON string as the delivered digital item.
  - Subsequent delivery attempts via `/deliver` append to existing deliverables rather than replacing them.
  - Fix: The webhook endpoint must return a human-friendly plain-text string ("Your Order has been Delivered! Please wait 10-15 minutes..."). Real deliverables are subsequently pushed via `POST /v1/shops/{shopId}/invoices/{invoiceId}/replace-delivered` using `invoice_item_id` (extracted from the webhook `item.id`), cleanly overwriting the placeholder.
- Database auto-migration on boot:
  - Deleting `resell.db` or starting on a clean container creates a 0-byte file that causes SQLite queries to fail with `sqlite3.OperationalError: no such table: orders`.
  - Fix: `Database.connect()` and FastAPI `lifespan` verify existence of `schema_migrations` and automatically apply migrations on startup and on any new connection.
  - In `webhook.py`, database insertions are wrapped in try/except to fail gracefully: if database persistence fails, an error is logged and the webhook still responds with HTTP 200 plain text so buyers never receive HTTP 500 errors.
- Supplier Privacy (Privacy Leak Prevention):
  - Suppliers email order details directly to the email provided in `create_checkout()`. Passing the customer's personal email leaks the customer's identity to the supplier and reveals the supplier to the customer.
  - Fix: Introduced `SUPPLIER_EMAIL` environment variable. `JobRunner` and `buy-test` send `SUPPLIER_EMAIL` to the supplier's checkout, while customer email is preserved for local store delivery and audit logs.

