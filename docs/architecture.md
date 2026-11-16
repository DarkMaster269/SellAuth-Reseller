# System Architecture & Fulfillment Pipeline

## 1. End-to-End Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Buyer
    participant Storefront as SellAuth Storefront
    participant Webhook as Webhook Receiver (/api/v1/deliver)
    participant DB as SQLite (OrdersRepo)
    participant Runner as JobRunner Engine
    participant Supplier as Supplier Storefront
    participant Electrum as Electrum-LTC Daemon

    Buyer->>Storefront: Places order & pays
    Storefront->>Webhook: HTTP POST /api/v1/deliver (HMAC signed)
    Webhook->>Webhook: Verify HMAC-SHA256 (raw bytes)
    Webhook->>DB: create_order_if_new() (state: received)
    Webhook->>Runner: submit(order_id)
    Webhook-->>Storefront: HTTP 200 OK (fast reply within <50ms)

    activate Runner
    Runner->>Supplier: create_checkout(variant, qty)
    Supplier-->>Runner: CheckoutRef (checkout_id, ltc_address, amount_sats)
    Runner->>DB: create_purchase() & transition(checkout_created)

    Runner->>Runner: validate_guards(margin, balance, spend caps)
    
    critical Global Payment Lock
        Runner->>Electrum: pay(address, amount_sats)
        Electrum-->>Runner: txid (Litecoin transaction hash)
    end

    Runner->>DB: update_purchase(txid) & transition(paid)

    loop Poll Supplier Status (every 0.5s)
        Runner->>Supplier: get_status(checkout_id)
        Supplier-->>Runner: status ("pending" / "confirming" / "completed")
        opt If elapsed > 180s (DELAYED_THRESHOLD_SECONDS)
            Runner->>DB: transition(delayed)
            Runner->>Storefront: notify_delayed(order)
        end
        opt If elapsed > 1800s (GIVEUP_THRESHOLD_SECONDS)
            Runner->>DB: transition(refund_needed)
            Runner-->>Runner: Fire Alert & Stop
        end
    end

    Runner->>Supplier: fetch_items(checkout_id)
    Supplier-->>Runner: items (keys, license strings)
    Runner->>DB: update_purchase(items) & transition(fetched)

    Runner->>Storefront: POST /v1/shops/{id}/invoices/{id}/deliver (items)
    Storefront-->>Runner: HTTP 200 (delivered)
    Runner->>DB: transition(delivered)
    deactivate Runner
```

---

## 2. Component Architecture

### Webhook Intake
- Handles incoming HTTPS requests on `POST /api/v1/deliver`.
- Enforces constant-time HMAC-SHA256 signature verification over raw incoming bytes.
- Guarantees immediate response (<100ms) to comply with SellAuth's 10-second timeout.
- Never holds HTTP connections open while waiting for cryptocurrency blockchain confirmations.

### Background Job Engine (`JobRunner`)
- Asynchronous task per order.
- Global serialization lock (`asyncio.Lock`) surrounding all `PaymentAdapter.pay()` calls to prevent concurrent wallet state corruption or double-spending.
- Crash recovery: On application startup, `runner.resume()` inspects non-terminal orders in SQLite and resumes fulfillment from their saved checkpoint.

### Fail-Closed Safety Matrix
- Any guard violation (e.g. margin below floor, float below reserve threshold, spend cap breach) immediately halts fulfillment and moves the order to `OrderState.REFUND_NEEDED`.
- Incomplete supplier deliverables (partial or 0 items) abort delivery and transition to `OrderState.REFUND_NEEDED`.
- Every transition writes an audit record to the `events` table atomically.

