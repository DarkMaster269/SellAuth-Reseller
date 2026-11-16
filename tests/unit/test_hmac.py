"""Unit tests for HMAC signature verification per Stage 3."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resell.app import create_app
from resell.config import Settings
from resell.db import Database
from resell.jobs import StubJobRunner
from resell.orders import OrdersRepo
from resell.webhook import compute_hmac


@pytest.fixture
def test_setup(tmp_path: Path) -> tuple[TestClient, str]:
    """Setup test client and known webhook secret."""
    secret = "test_super_secret_key_12345"
    db_path = tmp_path / "test_hmac.db"
    database = Database(db_path)
    database.apply_migrations()
    repo = OrdersRepo(database)
    runner = StubJobRunner()

    settings = Settings(
        SELLAUTH_API_KEY="sk_test_fake123",
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
        ELECTRUM_RPC_USER="user",
        ELECTRUM_RPC_PASSWORD="pwd",
    )

    app = create_app(settings=settings, db=database, repo=repo, runner=runner)
    client = TestClient(app)
    return client, secret


def test_valid_signature_accepted(test_setup: tuple[TestClient, str]) -> None:
    """Valid HMAC signature on raw body must return 200 OK."""
    client, secret = test_setup
    payload = {
        "invoice_id": 9001,
        "email": "buyer@test.com",
        "currency": "USD",
        "item": {
            "id": 8001,
            "product_id": 7001,
            "variant_id": 2092559,
            "quantity": 1,
            "total_price_usd": "5.00",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_hmac(secret, raw_body)

    resp = client.post(
        "/api/v1/deliver",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_valid_signature_with_sha256_prefix_accepted(test_setup: tuple[TestClient, str]) -> None:
    """Signature prefixed with sha256= must be accepted."""
    client, secret = test_setup
    payload = {
        "invoice_id": 9002,
        "email": "buyer@test.com",
        "currency": "USD",
        "item": {
            "id": 8002,
            "product_id": 7001,
            "variant_id": 2092559,
            "quantity": 1,
            "total_price_usd": "5.00",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = f"sha256={compute_hmac(secret, raw_body)}"

    resp = client.post(
        "/api/v1/deliver",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert resp.status_code == 200


def test_missing_signature_rejected(test_setup: tuple[TestClient, str]) -> None:
    """Missing X-Signature header must return 401 Unauthorized."""
    client, _ = test_setup
    raw_body = b'{"invoice_id": 9003}'

    resp = client.post(
        "/api/v1/deliver",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_wrong_signature_rejected(test_setup: tuple[TestClient, str]) -> None:
    """Incorrect HMAC signature must return 401 Unauthorized."""
    client, _ = test_setup
    raw_body = b'{"invoice_id": 9004}'
    fake_sig = "0" * 64

    resp = client.post(
        "/api/v1/deliver",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Signature": fake_sig},
    )
    assert resp.status_code == 401


def test_malformed_signature_rejected(test_setup: tuple[TestClient, str]) -> None:
    """Malformed non-hex signature must return 401 Unauthorized."""
    client, _ = test_setup
    raw_body = b'{"invoice_id": 9005}'

    resp = client.post(
        "/api/v1/deliver",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Signature": "invalid_not_hex!!!"},
    )
    assert resp.status_code == 401


def test_tampered_body_rejected(test_setup: tuple[TestClient, str]) -> None:
    """Body tampered after signing (even trailing whitespace) must fail signature verification."""
    client, secret = test_setup
    original_body = b'{"invoice_id": 9006, "email": "a@b.com"}'
    sig = compute_hmac(secret, original_body)

    # Add single trailing space to body
    tampered_body = original_body + b" "

    resp = client.post(
        "/api/v1/deliver",
        content=tampered_body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert resp.status_code == 401
