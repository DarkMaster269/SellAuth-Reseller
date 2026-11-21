# resell-sellauth

Automated reselling for SellAuth stores. Buyer pays on your store, a
job buys the same item on a supplier's store, and the delivered items
are attached to your buyer's invoice.

Built to survive webhook retries, crashes, slow Litecoin confirmations,
and supplier-side changes without ever buying twice or losing a paid
order.

## How it works (in one paragraph)

SellAuth sends a webhook when a buyer pays. The webhook returns 200
immediately (SellAuth times out after 10 seconds, so holding the
connection open while waiting for LTC confirmations is impossible).
A background job creates a checkout on the supplier's store, checks the
price against a margin floor, pays in Litecoin from your wallet, waits
for the supplier to confirm, fetches the delivered items, and calls
SellAuth's `POST /invoices/{id}/deliver` to hand them to your buyer.
Every state change is saved to SQLite before the next step, so a crash
resumes instead of repeating.

## Read these first (for Agents and Humans)

1. `agents.md` — the behavior contract for the building agent.
2. `detailed_implementation_plan.md` — the stage-by-stage plan.
3. `HUMAN_CHECKS.md` — what only you can do.
4. `files.md` — every file and why it exists.
5. `TESTING.md` — how to test.
6. `SECURITY.md` — what must never happen.
7. `docs/findings.md` — the facts Stage 0 produced.

## Quick start (local development)

```bash
# 1. Setup virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -e .

# 3. Setup environment variables
cp .env.example .env
# Open .env and fill in the fake/real values (never commit .env)

# 4. Run tests (must pass before doing anything else)
pytest

# 5. Run the local dev server
uvicorn resell.app:app --reload
```

## Status

- **Current Stage:** Stages 1-10 Complete (Ready for Stage 11: Deployment)
- **Completed Stages:**
  - **Stage 0:** Discovery & Fixture Harvesting (Webhook capture, 503 challenge analysis, permissions).
  - **Stage 1:** Environment, Configuration, Redaction Filters, and Virtual Environment.
  - **Stage 2:** SQLite Schema Migrations and Strict Order State Machine.
  - **Stage 3:** Fast Webhook Receiver with Raw HMAC-SHA256 Verification and Idempotency.
  - **Stage 4:** Background Job Runner Engine, Timeouts, and Strict Safety Invariant Guards.
  - **Stage 5:** Supplier Scraper with Dynamic 503 PoW Challenge Solver and Altcha Solver.
  - **Stage 6:** Stock Synchronization Engine (`max(0, supplier - margin - in_flight)`).
  - **Stage 7:** Electrum-LTC JSON-RPC Wallet Integration and Balance Querying.
  - **Stage 8:** Supplier Checkout Creation, LTC Payment Broadcast, and Kill-and-Resume Drill.
  - **Stage 9:** Customer Fulfillment via Plain-Text Webhook Placeholder and `/replace-delivered`.
  - **Stage 10:** Multi-Webhook Discord Dashboard (ASCII Only), Jittered Auto-Sync, Auto-Disabling Faulty Products, Emergency Killswitch, and Daily Profit Report.
- **Test Suite:** 135 tests passing (0 failing, 2 opt-in live/money tests skipped).
- **Linting:** 100% clean (`ruff check .`).

## CLI Commands

- `py -m resell inspect <url>`: Fetch and display live supplier variants, stock, and pricing.
- `py -m resell sync`: Run one-off stock synchronization from supplier to your SellAuth storefront.
- `py -m resell pay-test [--address <addr>] [--sats <amt>]`: Query wallet balance or send a test LTC payment.
- `py -m resell buy-test [--variant-id <id>] [--quantity <qty>]`: Create test supplier checkout without broadcasting payment.
- `py -m resell resume`: Resume and fulfill all pending active orders saved in SQLite.
- `py -m resell killswitch [--resume]`: Zero all store stock and pause order processing (or `--resume` to disengage).
- `py -m resell report [--hours 24]`: Print daily revenue, cost, and profit summary and dispatch to Discord.

## Non-goals (What this project will NOT do)

- **No held-open webhooks.** SellAuth's 10s timeout makes them impossible.
- **No hand-rolled Litecoin signing.** Electrum-LTC handles coin selection, fees, and signing.
- **No auto-refunds** until SellAuth's refund API is confirmed in Stage 0.
- **No auto price-sync** until a throwaway product test proves it is safe to update prices without blanking other product fields.

## License

The Altcha solver is reused from `DraxonV1/RESellAuth` (MIT). Keep the
notice.
