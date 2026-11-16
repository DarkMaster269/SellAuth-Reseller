# SECURITY.md — What must never happen

## Wallet keys

- The Litecoin wallet seed or private key never appears in the repo,
  the database, a fixture, a test, a log line, a commit, or a chat.
- It lives in one of: an environment variable, a file readable only by
  the service user and excluded by `.gitignore`, or the Electrum-LTC
  daemon's own wallet store (preferred).
- The agent must refuse to read or write it, and must never ask for it.
- If the agent sees a value that looks like a seed or private key, it
  stops, tells the human, and suggests rotating the wallet.

## API keys

- SellAuth seller API key: same rules as the wallet key. Backend only.
- Supplier API keys (if you go the Reseller route): same rules.
- `logging_setup.py` installs a redaction filter on every logger at
  import. Any `*_KEY`, `*_SECRET`, `*_TOKEN`, `Authorization`,
  `X-Signature`, and any LTC address or email is masked to
  `<redacted:name>`.

## Fail-closed matrix

| Situation                           | Stock              | Order state                  | Alert |
| ----------------------------------- | ------------------ | ---------------------------- | ----- |
| Supplier fetch error                | 0                  | `refund_needed` if mid-order | yes   |
| Supplier parse error                | 0                  | `refund_needed` if mid-order | yes   |
| Supplier blocked (403/503 unsolved) | 0                  | `refund_needed` if mid-order | yes   |
| Missing variant in mapping          | 0 for that variant | `refund_needed`              | yes   |
| Store API auth error                | 0                  | `refund_needed`              | yes   |
| Wallet balance below threshold      | 0 (all)            | in-flight continue           | yes   |
| Spend cap exceeded                  | keep current       | `refund_needed`              | yes   |
| Margin floor not met                | keep current       | `refund_needed`              | yes   |
| Any uncaught exception in job       | 0 for that variant | `refund_needed`              | yes   |

When in doubt: 0 stock, `refund_needed`, alert. Never the other way.

## Spend caps

Enforced in `guards.py`, checked before every `pay`:

- Per-purchase cap (sats): `MAX_SPEND_PER_ORDER_SATS`.
- Daily cap (sats): `MAX_SPEND_PER_DAY_SATS`.
- Wallet balance floor (sats): `MIN_WALLET_BALANCE_SATS` — below this,
  stock goes to 0 and no new purchases start.

Caps are read once at startup and never relaxed at runtime. To change
them, restart with new env vars, which is a human action.

## Kill switch

`python -m resell killswitch`:

1. Sets every mapped variant's stock to 0 via the store client.
2. Pauses the job runner (new orders return 200 but do not submit).
3. Sends an alert.
4. Leaves in-flight orders running so nothing is abandoned mid-payment.

This command is safe to run at any time and must be tested in Stage 10.

## Logging redaction checklist

Before closing any stage, run:

```bash
pytest tests/unit/test_logging_redaction.py -v
```

It must pass. If you add a new log line that includes buyer data, add
a case for it to that test.
