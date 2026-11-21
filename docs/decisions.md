# Decisions Log

Append-only record of non-obvious engineering decisions and their rationale.

- **2026-11-16 (Stage 0): Delivery Mode:** Late delivery only (`POST /v1/shops/{shopId}/invoices/{invoiceId}/deliver`). Reason: SellAuth notification timeouts (10s max) make synchronous held-open delivery during crypto confirmation impossible.
- **2026-11-16 (Stage 0): Anti-Bot Scraper Architecture:** Supplier storefronts enforce Cloudflare/Altcha 503 challenges. Scraper in Stage 5 will use `curl_cffi` browser impersonation and the Altcha challenge solver.
- **2026-11-16 (Stage 1): Money & Currency Types:** Integers for Satoshis (1 LTC = 100,000,000 sats) and `Decimal` for all USD values. Floats are strictly prohibited to prevent financial rounding errors.
- **2026-11-16 (Stage 1): Redaction Filter on Import:** `logging_setup.py` hooks globally into root logger on import so no unredacted PII (emails, LTC addresses, API keys, delivered items) can leak into console or files.
- **2026-11-16 (Stage 1): Time Injection:** All time-sensitive modules must accept a `Clock` protocol defaulting to `RealClock`, with `FakeClock` used in all tests to guarantee deterministic execution without sleeps.
- **2026-11-16 (Stage 5): Storefront HTML Parsing Strategy:** Primary extraction targets Alpine.js `productForm` reactive data object embedded in page scripts, with Schema.org `application/ld+json` as secondary fallback.
- **2026-11-16 (Stage 5): Altcha Proof-of-Work Challenge Solver:** Challenge solver isolated in `resell/altcha.py` retaining MIT license notice from `DraxonV1/RESellAuth` reference implementation.
- **2026-11-16 (Stage 5): SellAuth Storefront 503 Obfuscated PoW Solver:** SellAuth storefront 503 challenges embed a rotated array (`a0_0x2a54`) with an IIFE shift hex. Solver in `resell/sellauth_503.py` reverses the rotation, extracts dynamic cookie name (e.g., `yX3`) and challenge string, and finds SHA-1 suffix matching target bytes `(0xb0, 0x0b)` at offset `n1`. The cookie is dynamically set on the session without hardcoding.
- **2026-11-17 (Stage 6): Stock Formula & In-Flight Accounting:** Stock calculation applies `max(0, supplier - safety_margin - in_flight)`. In-flight counts all active database orders in non-terminal states to prevent overselling while crypto confirmations are pending.
- **2026-11-17 (Stage 6): Fail-Closed Stock Safety:** If supplier scraping encounters errors or storefront authentication fails, stock immediately fails-closed to 0 on the store and an error/alert is triggered. Authentication errors (401/403) surface immediately as `StoreAuthError` and are never swallowed.
- **2026-11-17 (Stage 7): Electrum-LTC JSON-RPC Calling Convention:** Calls `getbalance` and standard two-phase payment (`payto` -> `broadcast`). Amount conversions use integer Satoshis internally and 8-decimal-place LTC strings for JSON-RPC parameters.
- **2026-11-17 (Stage 7): Real Money Test Protection:** Real Litecoin broadcast tests are gated by `@pytest.mark.money` and require explicit `ALLOW_MONEY_TESTS=1`. Default `pay-test` CLI command only queries balance unless explicit destination and permission flag are provided.
- **2026-11-17 (Stage 8): Kill-and-Resume & Deduplication Safety:** In `JobRunner`, before broadcasting payment via Electrum-LTC, `purchase.txid` is re-inspected from the repository. If `txid` is already populated (e.g. process crashed or was killed immediately after `payment.pay()` broadcast), the payment is never re-broadcast and the order immediately advances to `PAID`.
- **2026-11-17 (Stage 8): Supplier Checkout & Gateway Locking:** `create_checkout` in `SupplierScraper` fetches the product page, solves the Altcha challenge widget if present, posts cart payload to `POST /v1/checkout`, and locks the LTC gateway via `POST /v1/invoices/{id}/payment` to extract address and satoshis.
- **2026-11-17 (Stage 8): Variant ID Whitespace Normalization:** Strip all variant IDs across parsing, configuration matching, and checkout payload construction (`str(v_id).strip()`) to prevent integer casting and backend validation failures on supplier storefronts with dirty HTML data.
- **2026-11-17 (Stage 8): Product ID Alpine JSON Parsing:** Extract product IDs using the parsed Alpine.js `productForm` object (`p_dict["id"]`) with non-greedy regex fallback `product\s*[:=]\s*\{[^{}]*?"id":\s*(\d+)` to prevent greedy matches jumping into nested category/group objects (`category: {"id": ...}`). Explicitly cast `productId` to `int` in the cart payload.
- **2026-11-17 (Stage 8): Dual-Mode SellAuth Checkout Support:** Support both legacy SellAuth (`/invoice/{id}` with `POST /v1/invoices/{id}/payment`) and modern SellAuth React storefronts (`/checkout/{unique_id}` with `PUT /v1/checkout/{unique_id}` using dynamic Altcha PoW). Dynamic Altcha challenges query `https://api-internal-3.sellauth.com/v1/altcha` whenever inline widget tags are absent.
- **2026-11-17 (Stage 8): Two-Step Buy-Test Drill:** `python -m resell buy-test` strictly stops after creating the local order and supplier checkout in SQLite, transitioning to `CHECKOUT_CREATED`, and printing the kill-and-resume drill message. It never executes `JobRunner`, broadcasts payments, or polls. Payment broadcasting is exclusively executed via `python -m resell resume`.
- **2026-11-17 (Stage 8): JobRunner.resume Order Collection:** `JobRunner.resume()` returns `list[Order]` representing all unresumed orders queued for execution, and CLI guards against `NoneType` to ensure clean reporting of resumed tasks.
- **2026-11-17 (Stage 8): Race Condition Prevention on Terminal Orders:** `_safe_transition` checks current DB state before performing transitions. If an order enters a terminal or fail-safe state (`refund_needed`, `refunded`, `delivered`), all delayed timers, item fetches, and polling loops immediately terminate without attempting illegal state transitions (such as `refund_needed` -> `delayed`).
- **2026-11-17 (Stage 8): Strict Electrum Broadcast Hex Validation:** In `payment_electrum.py`, `pay()` strictly asserts that `broadcast` returns a valid 64-character hexadecimal transaction ID. Any failure, empty response, or rejected broadcast raises `PaymentBroadcastError` and is never swallowed.
- **2026-11-17 (Stage 8): Fail-Closed Payment Transition Invariant:** `JobRunner` only advances an order to `OrderState.PAID` if `payment.pay()` yields a verified non-empty string `txid`. If broadcasting fails or returns an empty `txid`, `_process_order` fails closed directly to `OrderState.REFUND_NEEDED`. Exception handlers check that the order is not already in `REFUND_NEEDED` before triggering transitions.
- **2026-11-18 (Stage 9): Late Delivery Implementation & Exponential Backoff:** Implemented `LateDeliveryAdapter` in `resell/adapters/delivery_late.py` hitting SellAuth's `POST /v1/shops/{shopId}/invoices/{invoiceId}/deliver`. Retries up to 3 times on transient network and 5xx errors with backoff. Fails fast on 401/403 (`DeliveryAuthError`) and 400/404/422 (`DeliveryError`) without retrying to guarantee fail-closed behavior. Wired into `resell/app.py` for live webhooks and `resell/cli.py` for resuming pending orders.
- **2026-11-22 (Stage 9): Plain-Text Webhook Placeholder & Replace-Delivered Overwrite:** SellAuth stores the immediate webhook response body as the initial invoice deliverable. Returning JSON causes SellAuth to render raw JSON to the customer and append rather than overwrite upon later delivery. To provide a professional customer UX, `webhook.py` returns a plain-text friendly placeholder ("Your Order has been Delivered! Please wait 10-15 minutes..."). The database orders schema was expanded via migration `002_add_invoice_item_id.sql` to capture `invoice_item_id` (from webhook payload `item.id`). `LateDeliveryAdapter` calls `POST /v1/shops/{shopId}/invoices/{invoiceId}/replace-delivered` with `{"invoice_item_id": ..., "replacements": items}` to cleanly overwrite the placeholder with the real tokens, falling back to `/deliver` if `invoice_item_id` is missing.
- **2026-11-22 (Stage 9): Auto-Migration On Startup & Database Graceful Fallback:** Ensured `Database.connect()` and Uvicorn `lifespan` automatically apply migrations (`001_initial.sql`, `002_add_invoice_item_id.sql`) if the SQLite DB file is deleted or table `schema_migrations` is missing. Wrapped order creation in `webhook.py` in a try/except block that logs database errors and still returns HTTP 200 with the friendly placeholder so buyers never see HTTP 500.
- **2026-11-22 (Stage 9): Privacy Protection via Supplier Email:** Added required `SUPPLIER_EMAIL` environment variable loaded in `config.py`. Updated `JobRunner` and `buy-test` CLI to pass `SUPPLIER_EMAIL` to `supplier.create_checkout()` instead of the buyer's email, preventing suppliers from discovering buyer identities or emailing buyers directly. Buyer email remains saved on local orders for delivery notifications.
- **2026-11-22 (Stage 10): Discord Monitoring & ASCII Constraint:** Implemented `DiscordAlerts` in `resell/alerts.py` routing structured embeds across four dedicated webhooks (`DISCORD_WEBHOOK_ORDERS`, `DISCORD_WEBHOOK_SUPPLIER`, `DISCORD_WEBHOOK_DELIVERED`, `DISCORD_WEBHOOK_ALERTS`). All embeds strictly enforce printable standard ASCII (no emojis, no unicode symbols). Critical alerts ping `<@&DISCORD_ADMIN_ROLE_ID>` if configured.
- **2026-11-22 (Stage 10): Emergency Killswitch Architecture:** Implemented file-based killswitch (`resell.killswitch`) engaged via `py -m resell killswitch`. Zeroes all mapped variant stocks on SellAuth via `update_stock()`, pauses `JobRunner` order processing, and sends a critical alert to Discord. Disengaged via `py -m resell killswitch --resume`.
- **2026-11-22 (Stage 10): Auto-Sync Jitter & Auto-Disabling Faulty Products:** In `StockSynchronizer`, added +/- 30s jitter to interval loops to avoid rate limits or fingerprinting. If scraping a variant encounters 3 consecutive failures (404, parse errors), its stock on SellAuth is automatically zeroed, the variant is disabled, and a critical alert is dispatched to Discord without halting synchronization of other variants.
- **2026-11-22 (Stage 10): Low Balance Cooldown & Daily Report:** Added 12-hour deduplication cooldown to low wallet balance alerts in `jobs.py`. Implemented `py -m resell report` aggregating orders created in the last 24h, calculating revenue, supplier cost, and net profit, and sending a daily report to `DISCORD_WEBHOOK_DELIVERED`.

---

## Final Architecture Summary (Stages 1-10 Production Baseline)

1. **Intake Tier (FastAPI Webhook):**
   - Receives SellAuth `INVOICE.ITEM.DELIVER-DYNAMIC` webhooks.
   - Verifies HMAC-SHA256 signature over raw request bytes using `X-Signature` header.
   - Responds immediately with HTTP 200 OK plain-text deliverable placeholder to comply with SellAuth's 10-second request timeout.
   - Extracts `invoice_item_id` (from `item.id`) and commits the order to SQLite under `OrderState.RECEIVED`.
   - Hands off order execution asynchronously to `JobRunner`.

2. **Persistence & State Machine (SQLite WAL):**
   - Tables: `orders`, `purchases`, `events`, `schema_migrations`.
   - SQLite configured with WAL mode (`PRAGMA journal_mode = WAL;`), foreign keys enabled, and busy timeout.
   - Auto-migrating connection factory recreates tables and applies unapplied migrations dynamically.
   - Strict transition table prevents illegal state jumps: `received` -> `checkout_created` -> `paid` -> `delayed` (optional) -> `fetched` -> `delivered` / `refund_needed` -> `refunded`.

3. **Supplier Scraper & Challenge Solvers:**
   - Handles both traditional SellAuth and modern React SPA storefronts (`/checkout/{uuid}`).
   - Solves dynamic Altcha Proof-of-Work CAPTCHAs.
   - Bypasses SellAuth 503 storefront anti-bot gates via dynamic de-obfuscation and SHA-1 challenge solving (`resell/sellauth_503.py`).
   - Uses `SUPPLIER_EMAIL` for checkout creation to protect customer privacy.

4. **Cryptocurrency Gateway (Electrum-LTC JSON-RPC):**
   - Local JSON-RPC 2.0 communication (`getbalance`, `payto`, `broadcast`).
   - Global asyncio lock serializes payments so only one Litecoin transaction broadcasts at a time.
   - Strictly enforces 64-character hexadecimal transaction IDs.
   - Re-checks `txid` persistence prior to broadcast to ensure crash safety and eliminate double-spend risk.

5. **Customer Fulfillment (`replace-delivered`):**
   - Calls `POST /v1/shops/{shopId}/invoices/{invoiceId}/replace-delivered` with `{"invoice_item_id": ..., "replacements": [...]}`.
   - Overwrites the initial plain-text placeholder with real digital item tokens and triggers SellAuth's customer email dispatch.

6. **Monitoring, Operations & Safety:**
   - 4-channel Discord monitoring dashboard with strict ASCII formatting and clean embed styling.
   - Jittered stock synchronization (`max(0, supplier - margin - in_flight)`).
   - Automated product disabling after 3 consecutive scraper failures.
   - Instant killswitch (`py -m resell killswitch`) zeroing stock and locking job fulfillment.
   - 24-hour financial audit reporting (`py -m resell report`).




