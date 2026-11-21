# Operations Runbook — Monitoring, Alerts, and Incident Response

This runbook documents operational procedures, alert interpretations, and incident remediation steps for `resell-sellauth`.

---

## 1. Discord Monitoring Architecture

Alerts are routed to 4 dedicated Discord webhook channels with strict ASCII formatting and color coding:

| Channel / Webhook Variable     | Color Code   | Severity | Purpose |
|--------------------------------|--------------|----------|---------|
| `DISCORD_WEBHOOK_ORDERS`       | Steel Blue   | Info     | Real-time customer order placements and app lifecycle events (`[ORDER RECEIVED]`, `[SYSTEM STARTUP]`, `[SYSTEM SHUTDOWN]`). |
| `DISCORD_WEBHOOK_SUPPLIER`     | Dark Purple  | Info     | Supplier checkout creation, locked LTC addresses, and broadcasted transaction IDs (`[SUPPLIER PURCHASE]`). |
| `DISCORD_WEBHOOK_DELIVERED`    | Forest Green | Success  | Successful order fulfillment, execution latency, net profit, and masked digital item tokens (`[ORDER DELIVERED]`). |
| `DISCORD_WEBHOOK_ALERTS`       | Amber / Red  | Warning / Critical | Operational anomalies, guard breaches, scraper failures, low wallet balances, and emergency killswitch activations. If Critical, pings `@&DISCORD_ADMIN_ROLE_ID`. |

---

## 2. Alert Playbooks

### `[ALERT] Low Electrum-LTC Wallet Balance`
- **Trigger**: Electrum-LTC wallet balance falls below `MIN_WALLET_BALANCE_SATS` (default 0.1 LTC / 10,000,000 sats). Rate-limited to once every 12 hours.
- **Impact**: Orders that would breach `MIN_WALLET_BALANCE_SATS` fail closed to `refund_needed` without sending funds.
- **Action**:
  1. Open Electrum-LTC and send additional LTC to your wallet address.
  2. Verify balance in CLI:
     ```powershell
     py -m resell pay-test
     ```
  3. Resume any orders held in pending state:
     ```powershell
     $env:ALLOW_MONEY_TESTS="1"
     py -m resell resume
     ```

### `[CRITICAL] Guard Breach on Order #X`
- **Trigger**: An order violated margin floor, per-purchase cap, daily spend cap, or float balance floor.
- **State Transition**: Order transitions immediately to `refund_needed`. No payment is broadcast.
- **Action**:
  1. Inspect the order in SQLite:
     ```powershell
     sqlite3 resell.db "SELECT * FROM orders WHERE id = X;"
     sqlite3 resell.db "SELECT * FROM events WHERE order_id = X;"
     ```
  2. If the supplier raised prices above your sell price, update `mapping.yaml` or adjust prices on SellAuth.
  3. Issue manual refund on SellAuth if appropriate.

### `[CRITICAL] Variant Disabled: <Name>`
- **Trigger**: Background scraper failed 3 consecutive times (404 Not Found, Parse Error, or Supplier Unavailable).
- **Automated Response**: Store stock for that variant is automatically set to 0 on SellAuth to prevent customer orders, and the variant is temporarily disabled in memory.
- **Action**:
  1. Inspect the supplier's product URL:
     ```powershell
     py -m resell inspect "https://<supplier-domain>/product/<slug>"
     ```
  2. Check if the supplier changed product slugs, removed variants, or changed page structure.
  3. Update `mapping.yaml` with the new slug or variant ID.
  4. Run manual stock synchronization:
     ```powershell
     py -m resell sync
     ```

### `[CRITICAL] Order #X Processing Error`
- **Trigger**: Unhandled exception during payment broadcast, supplier confirmation polling, or delivery replacement.
- **State Transition**: Order transitions to `refund_needed`.
- **Action**:
  1. Check terminal logs or review `resell.db` purchase record:
     ```powershell
     sqlite3 resell.db "SELECT * FROM purchases WHERE order_id = X;"
     ```
  2. If a `txid` exists on the purchase record, Litecoin was sent. Check if the supplier delivered items or if confirmation timed out.
  3. If items were fetched but delivery failed due to network timeout, deliver manually to the customer invoice on SellAuth.

---

## 3. Emergency Operations

### Engaging the Killswitch
If you suspect an active exploit, supplier price glitch, or store failure, immediately engage the killswitch:
```powershell
py -m resell killswitch
```
- Sets stock of **all** mapped variants to 0 on SellAuth immediately.
- Creates `resell.killswitch` lock file, pausing background fulfillment.
- Dispatches a deep red Critical Alert to `DISCORD_WEBHOOK_ALERTS` pinging the admin role.

### Disengaging the Killswitch
Once the incident is resolved:
```powershell
py -m resell killswitch --resume
py -m resell sync
```
- Removes `resell.killswitch` lock file.
- Re-synchronizes live supplier stock to SellAuth storefront.

---

## 4. Daily Reconciliation & Profit Reporting

Run daily profit audit via CLI:
```powershell
py -m resell report
```
- Aggregates orders and purchases from the last 24 hours.
- Calculates total orders, total USD revenue, total supplier cost, and net profit.
- Prints summary table to stdout and dispatches an embed to `DISCORD_WEBHOOK_DELIVERED`.
- Optional custom timeframe: `py -m resell report --hours 48`.
