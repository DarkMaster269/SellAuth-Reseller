# TESTING.md — How to test this project

## Five levels

| Level    | Path              | Network         | Default | Marker               |
| -------- | ----------------- | --------------- | ------- | -------------------- |
| Unit     | `tests/unit/`     | Blocked         | Runs    | none                 |
| Contract | `tests/contract/` | Blocked         | Runs    | none                 |
| Chaos    | `tests/chaos/`    | Blocked         | Runs    | none                 |
| Live     | `tests/live/`     | Allowed         | Skipped | `@pytest.mark.live`  |
| Money    | `tests/money/`    | Allowed, spends | Skipped | `@pytest.mark.money` |

`pytest-socket` is configured in `tests/conftest.py` to raise on any
socket use during the first three levels. This is not negotiable: if a
test tries to reach the internet, it fails.

## Commands

```bash
# Everything the agent may run unattended
pytest

# One level
pytest tests/unit
pytest tests/contract
pytest tests/chaos

# One file
pytest tests/unit/test_orders_repo.py -v

# Read-only against real suppliers (needs network, no spend)
pytest tests/live --run-live

# Spends real LTC. Human starts it. Agent must not.
ALLOW_MONEY_TESTS=1 pytest tests/money
```

`pytest` with no args must pass before any stage is accepted.

## What each level proves

- **Unit** — pure logic: state machine, guards, formula, parsers, HMAC.
  No DB, no network, no clock.
- **Contract** — parsers against saved real artifacts: the redacted
  webhook body, saved supplier pages, saved API responses. Proves you
  parse reality, not a guess.
- **Chaos** — the whole `JobRunner` against fakes with `FakeClock`.
  Proves no double pay, no lost order, no overlap, crash-safe resume.
  If a chaos test is flaky, the bug is in `jobs.py`, not in the test.
- **Live** — one real supplier page, read only. Proves the scraper still
  works against reality. Run this after every supplier page change.
- **Money** — one real tiny payment to your own address. Proves the
  Electrum adapter works. Run once per wallet change, never casually.

## Fixtures

`tests/fixtures/` holds committed real artifacts. Rules:

- Emails redacted.
- LTC addresses replaced with `bc1qFAKE...`.
- No secrets.
- If a fixture is missing, ask the human. Do not synthesize a fake
  supplier page and call it real.
- When a supplier page changes, save the new one, keep the old one,
  and update the contract test to parse both if possible.

## Determinism

- All chaos tests use `FakeClock`. No `time.sleep`, no `asyncio.sleep`
  in production code paths that tests exercise.
- No random unless seeded and logged.
- No network in default levels.
- No reliance on test ordering.

## Pre-commit checklist for the agent

Before claiming a stage is done, run these four checks. Paste the exact output into your closing message. Do not just say "it works."

```bash
# 1. Linting must be clean
ruff check .

# 2. The full test suite must pass
pytest

# 3. No secrets or DBs staged for commit
git status --short

# 4. Deep scan for accidental secrets
grep -r "sk_live\|bc1q[0-9a-z]\{25,\}" . --include="*.py" --include="*.yaml" --include="*.md"
```
