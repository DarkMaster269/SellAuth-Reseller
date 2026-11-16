# detailed_implementation_plan.md — Step-by-step build guide

This plan is executed sequentially. Do not skip ahead.

**Rules for the Agent:**

- Read `agents.md` first.
- At the end of each stage, stop and post the `STAGE <N> COMPLETE` block.
- For Stage 2 and Stage 4, write the tests _first_, show they fail, then write the code.
- If a step says `HUMAN CHECK`, stop and wait for the human.

---

## Stage 0: Discovery (Human-Led)

**Goal:** Replace every assumption with recorded facts.
**Agent Role:** Create the scratch tools and wait.
**Money Risk:** None.

**Steps:**

1. Agent: Create `scratch/request_catcher.py` (FastAPI app to log webhooks and verify HMAC).
2. Agent: Create `scratch/probe_supplier.py` (Read-only supplier page parser).
3. Agent: Create `docs/findings.md` using the template in `files.md`.
4. **HUMAN CHECK:**
   - Create a throwaway product on SellAuth with Dynamic Delivery pointing to the `request_catcher.py` via an HTTPS tunnel.
   - Place test orders, save one raw redacted webhook payload to `tests/fixtures/webhook_payloads/`.
   - Fill out `docs/findings.md` with the real field names from the payload.
   - Confirm SellAuth's refund endpoint and record it.
   - Ask suppliers: required confirmations, min/max qty, permission for automated buying. Save replies in `docs/permissions/`.
   - Ask SellAuth support if automated purchasing is allowed.
5. **Exit:** `docs/findings.md` is complete. If SellAuth says automated purchasing is _not_ allowed, the human must abort the project.

---

## Stage 1: Skeleton, Config, and Logging

**Goal:** A clean, installable Python project with safe config loading and log redaction.
**Money Risk:** None.

**Steps:**

1. Agent: Create `pyproject.toml` (pinned deps: fastapi, pydantic-settings, pytest, pytest-asyncio, pytest-socket, ruff, curl_cffi) and generate the lockfile.
2. Agent: Create `.gitignore` (ignore `.env`, `*.db`, etc.).
3. Agent: Create `config.py` using `pydantic-settings` to load env vars. Fails loudly if missing.
4. Agent: Create `logging_setup.py` with a filter that redacts passwords, emails, LTC addresses, and item strings.
5. Agent: Create `clock.py` with `RealClock` and `FakeClock`.
6. Agent: Create `.github/workflows/ci.yml` to run `ruff` and `pytest`.
7. Agent: Write `tests/unit/test_config.py`, `test_logging_redaction.py`, `test_clock.py`.
8. Agent: Run `pytest`.
9. **HUMAN CHECK:**
   - Copy `.env.example` to `.env` and fill in real values.
   - Run a secret scanner (e.g., `gitleaks`) and confirm no secrets are committed.
10. **Exit:** CI is green. Agent posts `STAGE 1 COMPLETE`.

---

## Stage 2: Database and Order State Machine

**Goal:** Rock-solid order state transitions and idempotency.
**Money Risk:** None.
**Rule:** TDD (Tests before code).

**Steps:**

1. Agent: Write `tests/unit/test_states.py` (assert illegal transitions raise errors) and `test_orders_repo.py` (assert duplicate creation returns the same row, 50 concurrent creates yield 1 row).
2. Agent: Run tests (they must fail).
3. Agent: Create `resell/migrations/001_initial.sql` (orders, purchases, events tables with UNIQUE constraints).
4. Agent: Create `states.py` and `models.py`.
5. Agent: Create `orders.py` (repo pattern: `create_order_if_new`, `transition`, etc.).
6. Agent: Create `db.py` (SQLite WAL mode, foreign keys on).
7. Agent: Run tests (they must pass).
8. **HUMAN CHECK:** Review the state machine in `docs/data-model.md` and confirm it matches your business logic.
9. **Exit:** Tests pass. Agent posts `STAGE 2 COMPLETE`.

---

## Stage 3: Webhook Receiver

**Goal:** Accept real SellAuth webhooks, verify HMAC, and queue jobs.
**Money Risk:** None.

**Steps:**

1. Agent: Create `webhook.py` (FastAPI endpoint `POST /api/v1/deliver`).
2. Agent: Implement HMAC-SHA256 verification on the _raw_ body bytes. Reject unauthorized requests immediately.
3. Agent: Parse the JSON using the exact field names recorded in `docs/findings.md`.
4. Agent: Call `orders.create_order_if_new` and then `JobRunner.submit(order_id)`.
5. Agent: Write `tests/unit/test_hmac.py` and `tests/contract/test_webhook_payload.py` (using the real redacted payload).
6. **HUMAN CHECK:** Expose the app via a tunnel. Point SellAuth's webhook to it. Place a real test order. Confirm the order row in SQLite is correct and the webhook returns 200 immediately.
7. **Exit:** All tests pass. Real webhook accepted. Agent posts `STAGE 3 COMPLETE`.

---

## Stage 4: Job Runner (The Core)

**Goal:** A fully chaos-tested background job runner that never double-buys.
**Money Risk:** None (uses Fakes).
**Rule:** TDD (Tests before code).

**Steps:**

1. Agent: Create adapter protocols (`supplier_base.py`, `payment_base.py`, `delivery_base.py`).
2. Agent: Create fakes (`supplier_fake.py`, `payment_fake.py`, `delivery_fake.py`).
3. Agent: Write the `tests/chaos/` suite (happy path, duplicates, concurrency, crash resume, delayed, give-up, partial delivery).
4. Agent: Run tests (they must fail).
5. Agent: Create `jobs.py` (asyncio task per order, payment lock, retries, timeouts). Create `guards.py` (margin, balance, spend caps).
6. Agent: Create `demo.py` (runs 3 simulated orders: normal, slow, refunded).
7. Agent: Run tests (they must pass).
8. **HUMAN CHECK:** Run `python -m resell.demo`. Read the event logs. Confirm the thresholds (delayed time, give-up time, margin floor, spend caps) make sense and record them in `docs/decisions.md`.
9. **Exit:** Chaos suite passes. Agent posts `STAGE 4 COMPLETE`.

---

## Stage 5: Supplier Adapter (Read-Only)

**Goal:** Parse supplier stock and prices reliably.
**Money Risk:** None.

**Steps:**

1. Agent: Create `supplier_scrape.py`. Implement `list_variants` only (fetch page, parse embedded JSON object, handle Altcha/503 if needed).
2. Agent: Reuse the Altcha solver from `DraxonV1/RESellAuth` (keep MIT notice).
3. Agent: Create `cli.py` with the `inspect <url>` command.
4. Agent: Write unit tests for parsing and Altcha. Write a `live` test for the real supplier page.
5. **HUMAN CHECK:** Run `python -m resell inspect <supplier-url>`. Compare the output with a browser. Save the HTML to `tests/fixtures/supplier_pages/`.
6. **Exit:** Inspect output matches reality. Agent posts `STAGE 5 COMPLETE`.

---

## Stage 6: Stock Sync to Your Store

**Goal:** Your storefront stock reflects supplier stock minus safety margins.
**Money Risk:** None.

**Steps:**

1. Agent: Create `store_client.py` (SellAuth API PUT stock endpoint).
2. Agent: Create `stock_sync.py` (loop every 90s, fail-closed to 0 on any error).
3. Agent: Write `tests/unit/test_stock_formula.py`.
4. **HUMAN CHECK:** On a throwaway product, verify storefront stock updates. Break the API key and verify it fails closed to 0 and alerts.
5. **Exit:** Stock updates correctly. Agent posts `STAGE 6 COMPLETE`.

---

## Stage 7: Payments

**Goal:** Electrum-LTC integration.
**Money Risk:** A few cents.

**Steps:**

1. Agent: Create `payment_electrum.py` (JSON-RPC over localhost).
2. Agent: Write `tests/money/test_tiny_payment.py` (marked `@pytest.mark.money`).
3. **HUMAN CHECK:** Set up Electrum-LTC wallet. Fund it with a small float. Run the money test once. Confirm txid on a block explorer.
4. **Exit:** Money test passes. Agent posts `STAGE 7 COMPLETE`.

---

## Stage 8: Real Purchase

**Goal:** Buy from the supplier via command line.
**Money Risk:** Small.

**Steps:**

1. Agent: Implement `create_checkout`, `lock_payment`, `get_status`, `fetch_items` in `supplier_scrape.py`.
2. Agent: Wire them into `jobs.py`. Implement the kill-and-resume logic for the txid.
3. **HUMAN CHECK:** Run `python -m resell buy-test`. Perform the kill-and-resume drill (kill the process mid-purchase, restart, confirm no double spend).
4. **Exit:** Real purchase successful. Agent posts `STAGE 8 COMPLETE`.

---

## Stage 9: Delivery

**Goal:** End-to-end flow.
**Money Risk:** Small.

**Steps:**

1. Agent: Implement `delivery_late.py` (SellAuth POST invoice deliver).
2. Agent: Wire `jobs.py` to call delivery after `fetch_items`.
3. **HUMAN CHECK:** Place 3 real test orders on your store. Ensure all 3 deliver. Ensure one slow order triggers the "delayed" buyer notification.
4. **Exit:** 3 orders delivered. Agent posts `STAGE 9 COMPLETE`.

---

## Stage 10: Safety and Alerts

**Goal:** Killswitch and alerts.
**Money Risk:** None.

**Steps:**

1. Agent: Create `alerts.py` (Discord/Telegram webhook).
2. Agent: Add `killswitch` and `report` to `cli.py`.
3. Agent: Create `docs/runbook.md` (what to do on each alert).
4. **HUMAN CHECK:** Fire a test alert. Run the killswitch and verify all store stock drops to 0.
5. **Exit:** Alerts work. Killswitch works. Agent posts `STAGE 10 COMPLETE`.

---

## Stage 11: Deploy

**Goal:** Production server.
**Money Risk:** None.

**Steps:**

1. Agent: Create `deploy/` files (Caddyfile, systemd services, backup script).
2. **HUMAN CHECK:** Provision the VPS. Set up HTTPS. Run the reboot drill and restore drill.
3. **Exit:** Server runs reliably. Agent posts `STAGE 11 COMPLETE`.

---

## Stage 12: Pilot

**Goal:** Go live safely.
**Money Risk:** Real.

**Steps:**

1. **HUMAN CHECK:**
   - Enable exactly ONE product on your live store.
   - Monitor the first 7 days daily.
   - Check wallet float.
   - Scale up to more products only after 7 clean days.
2. **Exit:** Project complete.
