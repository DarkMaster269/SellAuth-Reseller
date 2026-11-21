"""Application configuration and variant mapping loader."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class VariantMapping(BaseModel):
    """Configuration for a single product variant mapped to a supplier."""

    our_variant_id: int
    our_product_id: int
    name: str
    supplier_domain: str
    supplier_product_slug: str
    supplier_variant_id: str
    min_margin_usd: Decimal = Field(default=Decimal("1.50"))
    max_price_usd: Decimal = Field(default=Decimal("10.00"))
    enabled: bool = True


class MappingConfig(BaseModel):
    """Container for mapped product variants."""

    variants: list[VariantMapping]

    def get_variant(self, our_variant_id: int) -> VariantMapping | None:
        """Find mapped variant by store variant ID."""
        for v in self.variants:
            if v.our_variant_id == our_variant_id:
                return v
        return None

    def find_variant(self, query: int | str) -> VariantMapping | None:
        """Find mapped variant by store variant ID or supplier variant ID."""
        query_str = str(query).strip()
        for v in self.variants:
            if (
                str(v.our_variant_id).strip() == query_str
                or str(v.supplier_variant_id).strip() == query_str
            ):
                return v
        return None


def load_mapping(path: Path | str = "mapping.yaml") -> MappingConfig:
    """Load and validate the variant-to-supplier mapping YAML file."""
    mapping_path = Path(path)
    if not mapping_path.is_file():
        raise FileNotFoundError(f"Mapping file not found at: {mapping_path}")

    raw_content = mapping_path.read_text(encoding="utf-8")
    parsed_yaml = yaml.safe_load(raw_content) or {}
    return MappingConfig.model_validate(parsed_yaml)


class Settings(BaseSettings):
    """Environment configuration validated at application startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SellAuth Store Settings
    SELLAUTH_API_KEY: SecretStr
    SELLAUTH_SHOP_ID: int
    SELLAUTH_WEBHOOK_SECRET: SecretStr

    # Database
    SQLITE_PATH: Path = Path("resell.db")

    # Mapping File
    MAPPING_FILE: Path = Path("mapping.yaml")

    # Safety & Spend Limits (in Satoshis)
    MAX_SPEND_PER_ORDER_SATS: int = 5000000  # 0.05 LTC
    MAX_SPEND_PER_DAY_SATS: int = 50000000   # 0.5 LTC
    MIN_WALLET_BALANCE_SATS: int = 10000000  # 0.1 LTC float floor

    # Margins and Timeouts
    MARGIN_FLOOR_USD: Decimal = Decimal("1.50")
    DELAYED_THRESHOLD_SECONDS: int = 180
    GIVEUP_THRESHOLD_SECONDS: int = 1800

    # Electrum-LTC RPC
    ELECTRUM_RPC_URL: str = "http://127.0.0.1:7773"
    ELECTRUM_RPC_USER: str = "electrum"
    ELECTRUM_RPC_PASSWORD: SecretStr = SecretStr("password")

    # Supplier Checkout Settings (email used at supplier; protects buyer privacy)
    SUPPLIER_EMAIL: str = "supplier_orders@example.com"

    # Alerts and Discord Webhooks (ASCII only, minimalist format)
    ALERT_WEBHOOK_URL: SecretStr | None = None
    DISCORD_WEBHOOK_ORDERS: SecretStr | None = None
    DISCORD_WEBHOOK_SUPPLIER: SecretStr | None = None
    DISCORD_WEBHOOK_DELIVERED: SecretStr | None = None
    DISCORD_WEBHOOK_ALERTS: SecretStr | None = None
    DISCORD_ADMIN_ROLE_ID: str | None = None

    # Stock Sync Automation
    STOCK_SYNC_INTERVAL_MINUTES: int = 5


def load_settings(_env_file: str | Path | None = ".env") -> Settings:
    """Load application settings from environment or specified .env file."""
    if _env_file is None:
        return Settings(_env_file=None)
    return Settings(_env_file=_env_file)
