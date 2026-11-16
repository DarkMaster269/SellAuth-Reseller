# HUMAN_CHECKS.md — Everything only you can do

The agent is not allowed to check these off. It must stop and wait for you.
Tick a box only after you have personally done the thing and saved any
evidence under `docs/`.

## Stage 0 — Discovery

- [ ] Created a throwaway product on my store with Dynamic Delivery pointing at `scratch/request_catcher.py` through an HTTPS tunnel.
- [ ] Placed test orders; saved one raw redacted webhook payload to `tests/fixtures/webhook_payloads/`.
- [ ] Recorded field names for invoice, item, product, variant, qty, email, price, currency, custom fields in `docs/findings.md`.
- [ ] Confirmed refund endpoint (exists? link?).
- [ ] Asked each supplier: confirmations required, min/max per variant, whether checkout holds stock, replacement policy, balance credit, written OK for automated purchasing. Saved in `docs/permissions/`.
- [ ] Asked SellAuth support whether automated purchasing from another store's checkout (including anti-bot solving) is allowed. Saved in `docs/permissions/sellauth-support-reply.md`.
- [ ] Recorded `DELIVERY_MODE`, `PAYMENT_RAIL`, delayed threshold, give-up threshold in `docs/decisions.md`.
- [ ] **If SellAuth said no, stopped the project.**

## Stage 1 — Skeleton

- [ ] Copied `.env.example` to `.env`, filled real values. Agent never sees them.
- [ ] Ran `gitleaks` over repo and history, found nothing.
- [ ] Confirmed `.env` and DB files are gitignored.

## Stage 2 — Database

- [ ] Read the state machine (Appendix B of the plan) and confirmed the states match how I think about an order, especially which states should alert me.

## Stage 3 — Webhook

- [ ] Ran the app, exposed through HTTPS tunnel, placed a real test order.
- [ ] Confirmed the order row has the correct invoice, item, product, variant, qty.
- [ ] Confirmed SellAuth accepted the placeholder response.

## Stage 4 — Job runner

- [ ] Ran `python -m resell.demo`, read all three event logs, they match expectations.
- [ ] Chose and wrote down: delayed threshold, give-up threshold, retry counts, margin floor, spend caps.

## Stage 5 — Supplier read-only

- [ ] Saved each supplier product's HTML source to `tests/fixtures/supplier_pages/` (emails redacted).
- [ ] Ran `python -m resell inspect <url>` for each and compared price, stock, min/max with a browser.
- [ ] Told the agent whether the 503 challenge page ever appears.
- [ ] Confirmed with each supplier how often they are happy for the script to read their page.

## Stage 6 — Stock sync

- [ ] On a throwaway product, storefront shows the right stock number.
- [ ] Mapped an out-of-stock variant, confirmed my store shows 0 within one interval.
- [ ] Temporarily broke the SellAuth API key, confirmed stock goes to 0 and I get an alert.
- [ ] (If enabled) confirmed price sync does not blank description, images, or other fields.

## Stage 7 — Payments

- [ ] Set up Electrum-LTC wallet, kept the seed offline.
- [ ] Funded a small float.
- [ ] Ran the `money` test once, sent a few cents, confirmed txid on a block explorer.

## Stage 8 — Real purchase

- [ ] Approved and ran `python -m resell buy-test` for one cheap variant.
- [ ] Did the kill-and-resume drill: killed the process mid-purchase, restarted, confirmed no double pay.

## Stage 9 — Delivery end to end

- [ ] Placed 3 real test orders on a throwaway product. All three delivered to the buyer.
- [ ] One of the three was slowed artificially (or naturally) to confirm late delivery works.

## Stage 10 — Safety

- [ ] Fired a test alert and confirmed it arrived on Discord/Telegram.
- [ ] Ran the kill switch, confirmed every variant stock goes to 0 and new jobs pause.
- [ ] Ran the daily report, confirmed numbers match manual arithmetic.

## Stage 11 — Deploy

- [ ] Server provisioned, HTTPS works, webhook reachable.
- [ ] Reboot drill: server restarts, jobs resume, no duplicates.
- [ ] Restore drill: restored a backup into a fresh path, integrity_check ok.

## Stage 12 — Pilot

- [ ] Live on one product only.
- [ ] Daily review for 7 days: all orders in the expected state, no refund_needed unless expected, wallet float healthy.
- [ ] Scaled to more products only after the 7 days.
