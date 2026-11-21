"""Contract tests verifying the webhook endpoint against real captured SellAuth payloads."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resell.app import create_app
from resell.config import MappingConfig, Settings, VariantMapping
from resell.db import Database
from resell.jobs import StubJobRunner
from resell.orders import OrdersRepo
from resell.states import OrderState
from resell.webhook import FRIENDLY_DELIVERY_PLACEHOLDER, compute_hmac

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "webhook_payloads"
REAL_PAYLOAD_PATH = FIXTURES_DIR / "test_order.json"


@pytest.fixture
def test_env(tmp_path: Path) -> tuple[TestClient, Database, OrdersRepo, StubJobRunner, str]:
    """Provide fully configured test client and database."""
    secret = "test_contract_webhook_secret_key"
    db_path = tmp_path / "contract_test.db"
    database = Database(db_path)
    database.apply_migrations()
    repo = OrdersRepo(database)
    runner = StubJobRunner()

    mapping = MappingConfig(
        variants=[
            VariantMapping(
                our_variant_id=2092559,
                our_product_id=996063,
                name="Default",
                supplier_domain="vna.mysellauth.com",
                supplier_product_slug="vna-3m-nitro",
                supplier_variant_id="var_nitro_3m",
                min_margin_usd=Decimal("1.50"),
                max_price_usd=Decimal("10.00"),
                enabled=True,
            )
        ]
    )

    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake_contract_key",
        SELLAUTH_SHOP_ID=226299,
        SELLAUTH_WEBHOOK_SECRET=secret,
        SQLITE_PATH=db_path,
        MAPPING_FILE=tmp_path / "mapping.yaml",
        MAX_SPEND_PER_ORDER_SATS=5000000,
        MAX_SPEND_PER_DAY_SATS=50000000,
        MIN_WALLET_BALANCE_SATS=10000000,
        MARGIN_FLOOR_USD=Decimal("1.50"),
        DELAYED_THRESHOLD_SECONDS=180,
        GIVEUP_THRESHOLD_SECONDS=1800,
        ELECTRUM_RPC_URL="http://127.0.0.1:7773",
        ELECTRUM_RPC_USER="electrum",
        ELECTRUM_RPC_PASSWORD="pwd",
    )

    app = create_app(
        settings=settings,
        db=database,
        repo=repo,
        runner=runner,
        mapping=mapping,
    )
    client = TestClient(app)
    return client, database, repo, runner, secret


def test_real_redacted_payload_parses_and_persists(
    test_env: tuple[TestClient, Database, OrdersRepo, StubJobRunner, str],
) -> None:
    """Real redacted SellAuth payload from Stage 0 is accepted and mapped accurately."""
    client, _, repo, runner, secret = test_env

    assert REAL_PAYLOAD_PATH.is_file(), f"Missing fixture at {REAL_PAYLOAD_PATH}"
    raw_payload = REAL_PAYLOAD_PATH.read_bytes()

    sig = compute_hmac(secret, raw_payload)

    # 1. Send webhook
    resp = client.post(
        "/api/v1/deliver",
        content=raw_payload,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sig,
            "Idempotency-Key": "test-idempotency-key-001",
        },
    )
    assert resp.status_code == 200
    assert resp.text == FRIENDLY_DELIVERY_PLACEHOLDER

    # 2. Assert order in SQLite matches real payload fields recorded in docs/findings.md
    order = repo.get_order_by_invoice(17506338)
    assert order is not None
    assert order.invoice_id == 17506338
    assert order.item_id == 16132365
    assert order.invoice_item_id == 16132365
    assert order.product_id == 996063
    assert order.variant_id == 2092559
    assert order.quantity == 2
    assert order.email == "buyer@example.com"
    assert order.price_paid_usd == Decimal("0.00")
    assert order.currency == "USD"
    assert order.state == OrderState.RECEIVED

    # 3. Assert background job runner received order ID
    assert order.id in runner.submitted_order_ids
    assert len(runner.submitted_order_ids) == 1

    # 4. Assert initial audit event created
    events = repo.get_events(order.id)
    assert len(events) == 1
    assert events[0].to_state == OrderState.RECEIVED


def test_real_payload_idempotent_duplicate_delivery(
    test_env: tuple[TestClient, Database, OrdersRepo, StubJobRunner, str],
) -> None:
    """Resending the exact same payload returns 200 without creating duplicate orders."""
    client, _, repo, runner, secret = test_env
    raw_payload = REAL_PAYLOAD_PATH.read_bytes()
    sig = compute_hmac(secret, raw_payload)

    headers = {"Content-Type": "application/json", "X-Signature": sig}

    # Delivery 1
    resp1 = client.post("/api/v1/deliver", content=raw_payload, headers=headers)
    assert resp1.status_code == 200
    assert resp1.text == FRIENDLY_DELIVERY_PLACEHOLDER

    # Delivery 2 (SellAuth retry)
    resp2 = client.post("/api/v1/deliver", content=raw_payload, headers=headers)
    assert resp2.status_code == 200
    assert resp2.text == FRIENDLY_DELIVERY_PLACEHOLDER

    # Ensure still only 1 row in database
    all_orders = repo.get_all_orders()
    assert len(all_orders) == 1
    assert all_orders[0].invoice_id == 17506338
    assert all_orders[0].invoice_item_id == 16132365

    # Ensure job runner was not submitted twice
    assert len(runner.submitted_order_ids) == 1


def test_invoice_item_id_extracted_and_persisted(
    test_env: tuple[TestClient, Database, OrdersRepo, StubJobRunner, str],
) -> None:
    """Ensure invoice_item_id is correctly extracted from item.id and saved to SQLite."""
    client, _, repo, _, secret = test_env
    raw_payload = REAL_PAYLOAD_PATH.read_bytes()
    sig = compute_hmac(secret, raw_payload)

    resp = client.post(
        "/api/v1/deliver",
        content=raw_payload,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert resp.status_code == 200
    assert resp.text == FRIENDLY_DELIVERY_PLACEHOLDER

    order = repo.get_order_by_invoice(17506338)
    assert order is not None
    assert order.invoice_item_id == 16132365
    assert order.item_id == 16132365


def test_webhook_database_failure_returns_200_placeholder(
    test_env: tuple[TestClient, Database, OrdersRepo, StubJobRunner, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If database raises OperationalError on write, webhook still returns 200 OK placeholder."""
    import sqlite3

    client, _, repo, _, secret = test_env
    raw_payload = REAL_PAYLOAD_PATH.read_bytes()
    sig = compute_hmac(secret, raw_payload)

    def failing_create(*args: object, **kwargs: object) -> tuple[object, bool]:
        raise sqlite3.OperationalError("no such table: orders")

    monkeypatch.setattr(repo, "create_order_if_new", failing_create)

    resp = client.post(
        "/api/v1/deliver",
        content=raw_payload,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    # Must fail gracefully with 200 OK and friendly placeholder, never 500
    assert resp.status_code == 200
    assert resp.text == FRIENDLY_DELIVERY_PLACEHOLDER

