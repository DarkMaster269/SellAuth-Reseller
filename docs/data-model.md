# Data Model & State Machine

## 1. Entity-Relationship Diagram

```mermaid
erDiagram
    orders ||--o| purchases : "has at most one"
    orders ||--o{ events : "audits state changes"

    orders {
        INTEGER id PK
        INTEGER invoice_id UK "SellAuth Invoice ID"
        INTEGER item_id "SellAuth Item ID"
        INTEGER product_id
        INTEGER variant_id
        INTEGER quantity
        TEXT email
        TEXT price_paid_usd "Decimal string"
        TEXT currency
        TEXT state "OrderState enum"
        TEXT created_at "ISO-8601 UTC"
        TEXT updated_at "ISO-8601 UTC"
    }

    purchases {
        INTEGER id PK
        INTEGER order_id FK,UK "1:1 with orders"
        TEXT supplier_domain
        TEXT supplier_product_slug
        TEXT supplier_variant_id
        TEXT supplier_checkout_id
        TEXT ltc_address
        INTEGER ltc_amount_sats "Satoshis"
        TEXT txid "Litecoin Transaction Hash"
        TEXT items_delivered "JSON Array"
        TEXT created_at "ISO-8601 UTC"
        TEXT updated_at "ISO-8601 UTC"
    }

    events {
        INTEGER id PK
        INTEGER order_id FK
        TEXT from_state
        TEXT to_state
        TEXT reason
        TEXT metadata "JSON Object"
        TEXT created_at "ISO-8601 UTC"
    }
```

---

## 2. Order State Machine

```mermaid
stateDiagram-v2
    [*] --> received : Webhook received

    received --> checkout_created : Supplier checkout created
    received --> refund_needed : Guard / Mapping failure

    checkout_created --> paid : LTC payment broadcasted
    checkout_created --> refund_needed : Checkout expired / price surge

    paid --> delayed : Threshold exceeded (>180s)
    paid --> fetched : Supplier items delivered
    paid --> refund_needed : Giveup timeout / Supplier cancelled

    delayed --> fetched : Supplier items confirmed
    delayed --> refund_needed : Giveup threshold reached (>1800s)

    fetched --> delivered : SellAuth late delivery API success
    fetched --> refund_needed : Delivery API permanent failure

    delivered --> [*] : Terminal (Order Complete)

    refund_needed --> refunded : Refund issued to customer
    refunded --> [*] : Terminal (Refund Complete)
```

---

## 3. Allowed Transitions Matrix (from `states.py`)

| Origin State (`from_state`) | Permissible Destination States (`to_state`) | Terminal? | Notes |
|:---|:---|:---:|:---|
| `received` | `checkout_created`, `refund_needed` | No | Initial state on webhook arrival |
| `checkout_created` | `paid`, `refund_needed` | No | Supplier checkout created & locked |
| `paid` | `delayed`, `fetched`, `refund_needed` | No | LTC transaction broadcasted |
| `delayed` | `fetched`, `refund_needed` | No | Buyer notified of confirmation delay |
| `fetched` | `delivered`, `refund_needed` | No | Deliverable items received from supplier |
| `delivered` | *(none)* | **Yes** | Items attached to SellAuth buyer invoice |
| `refund_needed` | `refunded` | No | Alert raised; requires manual/auto refund |
| `refunded` | *(none)* | **Yes** | Customer refunded |

---

## 4. Invariant Rules & Guarantees

1. **Idempotent Webhook Intake:**
   `orders.invoice_id` is indexed with a `UNIQUE` constraint. Duplicate webhook deliveries from SellAuth return the existing order without inserting duplicates or resetting progress.
2. **Strict Single-Purchase Guarantee:**
   `purchases.order_id` has a `UNIQUE` foreign key constraint. The repository enforces that no more than one purchase can ever be created for an order.
3. **Atomic Audit Logging:**
   Every transition in `OrdersRepo.transition()` updates `orders.state` and inserts an audit log row into `events` inside a single `BEGIN IMMEDIATE` SQLite transaction.
4. **WAL Mode & Busy Timeout:**
   SQLite connections operate with `PRAGMA journal_mode = WAL`, `PRAGMA foreign_keys = ON`, and a 5000ms `busy_timeout` to support high concurrency safely without table locks.

