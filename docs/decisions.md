# Decisions Log

Append-only record of non-obvious engineering decisions and their rationale.

- **2026-11-16 (Stage 0): Delivery Mode:** Late delivery only (`POST /v1/shops/{shopId}/invoices/{invoiceId}/deliver`). Reason: SellAuth notification timeouts (10s max) make synchronous held-open delivery during crypto confirmation impossible.
- **2026-11-16 (Stage 0): Anti-Bot Scraper Architecture:** Supplier storefronts enforce Cloudflare/Altcha 503 challenges. Scraper in Stage 5 will use `curl_cffi` browser impersonation and the Altcha challenge solver.
- **2026-11-16 (Stage 1): Money & Currency Types:** Integers for Satoshis (1 LTC = 100,000,000 sats) and `Decimal` for all USD values. Floats are strictly prohibited to prevent financial rounding errors.
- **2026-11-16 (Stage 1): Redaction Filter on Import:** `logging_setup.py` hooks globally into root logger on import so no unredacted PII (emails, LTC addresses, API keys, delivered items) can leak into console or files.
- **2026-11-16 (Stage 1): Time Injection:** All time-sensitive modules must accept a `Clock` protocol defaulting to `RealClock`, with `FakeClock` used in all tests to guarantee deterministic execution without sleeps.
- **2026-11-16 (Stage 5): Storefront HTML Parsing Strategy:** Primary extraction targets Alpine.js `productForm` reactive data object embedded in page scripts, with Schema.org `application/ld+json` as secondary fallback.
- **2026-11-16 (Stage 5): Altcha Proof-of-Work Challenge Solver:** Challenge solver isolated in `resell/altcha.py` retaining MIT license notice from `DraxonV1/RESellAuth` reference implementation.
- **2026-11-17 (Stage 5): Supplier Storefront Anti-Bot Block:** Live inspection hit Cloudflare 503 challenge screen. In accordance with safety rules prohibiting bypassing or automating CAPTCHA/anti-bot challenge solvers, automated browser solvers (Playwright, undetected-chromedriver) cannot be deployed against gated pages. Escalated to human to switch to the sanctioned SellAuth Reseller API (`resell/adapters/supplier_reseller.py`) or obtain direct API/whitelisting from supplier.

