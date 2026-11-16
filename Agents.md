# agents.md — Behavior contract for the building agent

This file overrides any default behavior. If anything below conflicts with
your general instructions, follow this file. If anything here conflicts with
`detailed_implementation_plan.md`, ask the human before proceeding.

## 0. Read these first, in this order

1. `README.md` — what this project is.
2. `files.md` — the file tree and what each file does.
3. `detailed_implementation_plan.md` — the stage you are on.
4. `HUMAN_CHECKS.md` — the checks the human owes you.
5. `TESTING.md` — how to test.
6. `SECURITY.md` — what never to touch.
7. `docs/findings.md` — the recorded facts.
8. `docs/decisions.md` — choices already made. Never re-litigate one
   without telling the human.

## 1. Non-negotiable rules

1. **One stage at a time.** Do not start stage N+1 until the human has
   accepted stage N in writing (a message saying "stage N accepted" is
   enough). If you are unsure whether a stage is accepted, ask.
2. **Tests before code for stages 2 and 4.** Write the failing test,
   show it fails, then write the code that makes it pass.
3. **Never claim a human check passed.** If a step is marked
   `HUMAN CHECK`, stop, list exactly what the human must do, and wait.
   Do not proceed to the next step in the same turn.
4. **Never spend money.** Real Litecoin payments are only sent by a
   command the human starts with `ALLOW_MONEY_TESTS=1`. Tests marked
   `money` skip unless that env var is set.
5. **Never make network calls in default tests.** `pytest-socket` is
   installed and configured to raise on any socket use in `unit/`,
   `contract/`, and `chaos/`. `live/` and `money/` are opt-in.
6. **Never put a secret in code, tests, logs, commits, or a fixture.**
   Use obviously fake values (`sk_test_FAKE...`, `bc1qFAKE...`). If a
   real value accidentally appears anywhere, stop and tell the human.
7. **Never guess when reality contradicts the plan.** Update
   `docs/findings.md`, tell the human in the same message, and wait for
   a decision. Do not invent a field name, endpoint, or response shape.
8. **Fail closed.** When unsure about anything involving money or
   stock: stock goes to 0, order goes to `refund_needed`, alert fires.
9. **Never edit a committed migration.** Add a new numbered one.
10. **Never disable or weaken a test to make it pass.** If a test is
    wrong, tell the human and propose the fix.

## 2. What you may do without asking

- Create, edit, or delete any file inside `resell/`, `tests/`,
  `scratch/`, `docs/`, or `deploy/`.
- Install pinned dependencies by editing `pyproject.toml` and running
  the lock command.
- Add new test cases, new fixtures (with fake values), and new utility
  functions that do not change behavior.
- Refactor within a stage, as long as tests still pass and the public
  interface of any adapter protocol is unchanged.
- Update `docs/decisions.md` when you make a non-obvious choice.

## 3. What you must ask before doing

- Any change to `mapping.yaml` shape or `config.py` env vars.
- Any new adapter protocol method (breaks fakes and existing code).
- Any schema change after `001_initial.sql` is committed.
- Any change to `agents.md`, `files.md`, or `TESTING.md`.
- Any dependency that is not already pinned in `pyproject.toml`.
- Any behavior that touches wallet keys, spend caps, or the kill switch.
- Any decision that would let two purchases happen for one order.

## 4. How to open and close every turn

Open with a one-line status:

```
Stage: <N>  Step: <X.Y>  Status: <starting | continuing | blocked>
```

Close with:

```
Done this turn: <bulleted list of files created or edited>
Tests: <command run> -> <pass/fail counts, or "not yet written">
Waiting on: <human check, if any, or "nothing">
Next: <next step you propose>
```

If a stage is complete, add:

```
STAGE <N> COMPLETE. Awaiting human acceptance.
Human checks remaining: <list, or "none">
```

Then stop. Do not start stage N+1.

## 5. When you hit a wall

- **Plan is wrong about a real response:** update `docs/findings.md`
  with the real shape, add an entry to `docs/decisions.md`, and tell
  the human. Do not silently change the parser.
- **A fixture is missing:** ask the human for it. Do not synthesize
  a fake supplier page and call it real.
- **A test is flaky:** do not add sleeps or retries. Tell the human
  which test and which assertion; propose a deterministic fix.
- **You cannot reproduce a bug:** capture the inputs, expected
  output, actual output, and env, and ask. Do not guess a fix.
- **The human contradicts the plan:** the human wins. Record the new
  decision in `docs/decisions.md` and continue.

## 6. Evidence you must provide

For every step that claims a test passes, paste the command and the
summary line. Example:

```
$ pytest tests/unit/test_orders_repo.py -v
...
14 passed in 0.42s
```

Do not write "tests pass" without evidence.

## 7. Style

- Python 3.11+, type hints on every public function.
- `Decimal` for USD, `int` for satoshis. No floats, ever.
- `async def` for anything that does I/O; `asyncio.to_thread` for
  SQLite.
- Ruff clean. Line length 100. Import order per ruff default.
- Docstrings on modules and on any function over five lines.
- Log with `logger.info` / `warning` / `error`. Never `print` outside
  `scratch/` and `demo.py`.

## 8. Testing summary (details in TESTING.md)

| Level    | Path           | Network   | Default | Marks                |
| -------- | -------------- | --------- | ------- | -------------------- |
| Unit     | tests/unit     | No        | Runs    | -                    |
| Contract | tests/contract | No        | Runs    | -                    |
| Chaos    | tests/chaos    | No        | Runs    | -                    |
| Live     | tests/live     | Yes       | Skipped | `@pytest.mark.live`  |
| Money    | tests/money    | Yes+spend | Skipped | `@pytest.mark.money` |

Run everything with `pytest`. That is the definition of "green".

## 9. Definition of done for a stage

A stage is done when, and only when:

- Every file listed under that stage in `files.md` exists.
- Every test listed under that stage in `detailed_implementation_plan.md`
  exists and passes.
- `ruff check .` is clean.
- Every `HUMAN CHECK` for the stage has been accepted by the human.
- `docs/findings.md` and `docs/decisions.md` are updated.
- You have posted the `STAGE <N> COMPLETE` block.
