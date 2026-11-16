"""Unit tests for configuration loading and validation per Stage 1."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from resell.config import load_mapping, load_settings


def test_config_loads_valid_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that valid configuration loads properly with typed fields."""
    mapping_file = tmp_path / "mapping.yaml"
    mapping_file.write_text(
        """
variants:
  - our_variant_id: 2092559
    our_product_id: 996063
    name: "Test Item"
    supplier_domain: "supplier.sellauth.com"
    supplier_product_slug: "test-slug"
    supplier_variant_id: "var_123"
    min_margin_usd: "2.00"
    max_price_usd: "15.00"
    enabled: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("SELLAUTH_API_KEY", "sk_test_FAKESELLAUTHKEY1234567890")
    monkeypatch.setenv("SELLAUTH_SHOP_ID", "226299")
    monkeypatch.setenv("SELLAUTH_WEBHOOK_SECRET", "fake_secret")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MAPPING_FILE", str(mapping_file))
    monkeypatch.setenv("MAX_SPEND_PER_ORDER_SATS", "5000000")
    monkeypatch.setenv("MAX_SPEND_PER_DAY_SATS", "50000000")
    monkeypatch.setenv("MIN_WALLET_BALANCE_SATS", "10000000")
    monkeypatch.setenv("MARGIN_FLOOR_USD", "1.50")
    monkeypatch.setenv("DELAYED_THRESHOLD_SECONDS", "180")
    monkeypatch.setenv("GIVEUP_THRESHOLD_SECONDS", "1800")
    monkeypatch.setenv("ELECTRUM_RPC_URL", "http://127.0.0.1:7773")
    monkeypatch.setenv("ELECTRUM_RPC_USER", "electrum_user")
    monkeypatch.setenv("ELECTRUM_RPC_PASSWORD", "electrum_pwd")

    settings = load_settings()

    assert settings.SELLAUTH_API_KEY.get_secret_value() == "sk_test_FAKESELLAUTHKEY1234567890"
    assert settings.SELLAUTH_SHOP_ID == 226299
    assert settings.MAX_SPEND_PER_ORDER_SATS == 5000000
    assert isinstance(settings.MAX_SPEND_PER_ORDER_SATS, int)
    assert settings.MARGIN_FLOOR_USD == Decimal("1.50")
    assert isinstance(settings.MARGIN_FLOOR_USD, Decimal)


def test_config_fails_loudly_on_missing_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing required settings must raise ValidationError with clear field names."""
    # Clear required environment variables
    for var in [
        "SELLAUTH_API_KEY",
        "SELLAUTH_SHOP_ID",
        "SELLAUTH_WEBHOOK_SECRET",
        "MAX_SPEND_PER_ORDER_SATS",
        "MAX_SPEND_PER_DAY_SATS",
        "MIN_WALLET_BALANCE_SATS",
    ]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(Exception) as exc_info:
        load_settings(_env_file=None)

    error_msg = str(exc_info.value)
    assert "SELLAUTH_API_KEY" in error_msg or "sellauth_api_key" in error_msg.lower()


def test_load_mapping_valid(tmp_path: Path) -> None:
    """Variant mapping loads correctly and maps our_variant_id to supplier details."""
    mapping_file = tmp_path / "mapping.yaml"
    mapping_file.write_text(
        """
variants:
  - our_variant_id: 111
    our_product_id: 222
    name: "Nitro 1M"
    supplier_domain: "vna.mysellauth.com"
    supplier_product_slug: "nitro-1m"
    supplier_variant_id: "sup_111"
    min_margin_usd: "1.25"
    max_price_usd: "8.50"
    enabled: true
""",
        encoding="utf-8",
    )

    mapping = load_mapping(mapping_file)
    assert len(mapping.variants) == 1
    var = mapping.get_variant(111)
    assert var is not None
    assert var.name == "Nitro 1M"
    assert var.min_margin_usd == Decimal("1.25")
    assert var.max_price_usd == Decimal("8.50")


def test_load_mapping_missing_file() -> None:
    """Missing mapping file must raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_mapping(Path("non_existent_mapping_file.yaml"))
