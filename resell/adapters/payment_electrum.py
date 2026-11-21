"""Electrum-LTC payment adapter communicating via JSON-RPC over localhost."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import httpx
from pydantic import SecretStr

from resell.adapters.payment_base import PaymentAdapter

logger = logging.getLogger(__name__)


class ElectrumError(RuntimeError):
    """Base error for Electrum-LTC adapter operations."""


class ElectrumConnectionError(ElectrumError):
    """Raised when Electrum JSON-RPC daemon is unreachable."""


class ElectrumRPCError(ElectrumError):
    """Raised when Electrum daemon returns a JSON-RPC error."""


class PaymentBroadcastError(ElectrumError):
    """Raised when a payment transaction is rejected during broadcast."""


def format_wallet_path(path: str) -> str:
    """Format Windows wallet path, replacing single backslashes with double backslashes."""
    if not path:
        return ""
    normalized = str(path).replace("\\\\", "\\")
    return normalized.replace("\\", "\\\\")


class ElectrumPaymentAdapter(PaymentAdapter):
    """Payment adapter implementation for Electrum-LTC JSON-RPC daemon."""

    def __init__(
        self,
        rpc_url: str,
        rpc_user: str,
        rpc_password: str | SecretStr,
        wallet_password: str | SecretStr | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.rpc_url = rpc_url
        self.rpc_user = rpc_user
        self.rpc_password = (
            rpc_password.get_secret_value()
            if isinstance(rpc_password, SecretStr)
            else rpc_password
        )
        self.wallet_password = (
            wallet_password.get_secret_value()
            if isinstance(wallet_password, SecretStr)
            else wallet_password
        ) or self.rpc_password
        self.timeout = timeout

    async def get_loaded_wallet_path(self) -> str | None:
        """Query Electrum daemon for currently loaded wallet path via list_wallets."""
        try:
            res = await self._call("list_wallets")
        except Exception as exc:
            logger.warning("Failed to query list_wallets: %s", exc)
            return None

        if not res:
            return None

        path: str | None = None
        if isinstance(res, list) and len(res) > 0:
            first = res[0]
            if isinstance(first, dict):
                path = first.get("path")
            else:
                path = str(first)
        elif isinstance(res, dict):
            path = res.get("path")
        elif isinstance(res, str):
            path = res

        return str(path).strip() if path else None

    async def _call(self, method: str, params: list[Any] | dict[str, Any] | None = None) -> Any:
        """Execute a JSON-RPC 2.0 call against Electrum daemon."""
        payload = {
            "jsonrpc": "2.0",
            "id": method,
            "method": method,
            "params": params if params is not None else [],
        }

        auth = httpx.BasicAuth(username=self.rpc_user, password=self.rpc_password)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    self.rpc_url,
                    content=json.dumps(payload),
                    auth=auth,
                    headers=headers,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.error("Failed to connect to Electrum daemon at %s: %s", self.rpc_url, exc)
            raise ElectrumConnectionError(
                f"Electrum daemon unreachable at {self.rpc_url}: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise ElectrumRPCError(
                f"Electrum daemon HTTP error {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        if "error" in data and data["error"] is not None:
            err = data["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise ElectrumRPCError(f"Electrum RPC error: {err_msg}")

        return data.get("result")

    async def balance_sats(self) -> int:
        """Query confirmed wallet balance in Satoshis."""
        wallet_path = await self.get_loaded_wallet_path()
        params: dict[str, Any] | list[Any]
        if wallet_path:
            formatted_path = format_wallet_path(wallet_path)
            params = {"wallet": formatted_path}
        else:
            params = []

        try:
            result = await self._call("getbalance", params)
        except ElectrumRPCError as exc:
            if wallet_path and "wallet not loaded" in str(exc).lower():
                result = await self._call("getbalance", [format_wallet_path(wallet_path)])
            else:
                raise

        raw_val = result.get("confirmed", 0) if isinstance(result, dict) else result
        val_str = str(raw_val).strip()

        if not val_str or val_str == "0":
            return 0

        val_dec = Decimal(val_str)
        # If value has decimals or is in standard LTC denomination (< 100,000 LTC)
        if "." in val_str or val_dec < Decimal("100000"):
            return int(val_dec * Decimal("100000000"))

        return int(val_dec)

    async def pay(self, address: str, amount_sats: int, order_id: int) -> str:
        """Create, sign, and broadcast a payment of amount_sats to address."""
        if not address or not address.strip():
            raise ValueError("Address cannot be empty.")
        if amount_sats <= 0:
            raise ValueError(f"Amount must be positive Satoshis (got {amount_sats}).")

        amount_ltc = f"{Decimal(amount_sats) / Decimal('100000000'):.8f}"
        logger.info(
            "Initiating payment for order=%s: amount_sats=%s (LTC %s)",
            order_id,
            amount_sats,
            amount_ltc,
        )
        print(
            f"[PAYMENT] Initiating pay() for order={order_id}: address={address}, "
            f"amount={amount_sats} sats ({amount_ltc} LTC)...",
            flush=True,
        )

        wallet_path = await self.get_loaded_wallet_path()
        call_params: dict[str, Any] | list[Any]
        if wallet_path:
            formatted_path = format_wallet_path(wallet_path)
            payto_dict: dict[str, Any] = {
                "destination": address.strip(),
                "amount": amount_ltc,
                "wallet": formatted_path,
            }
            if self.wallet_password:
                payto_dict["password"] = self.wallet_password
            call_params = payto_dict
        else:
            call_params = [address.strip(), amount_ltc]

        # 1. Create and sign transaction via payto
        try:
            payto_res = await self._call("payto", call_params)
        except ElectrumRPCError as exc:
            is_unloaded = "wallet not loaded" in str(exc).lower()
            if wallet_path and isinstance(call_params, dict) and is_unloaded:
                payto_res = await self._call(
                    "payto",
                    [address.strip(), amount_ltc, format_wallet_path(wallet_path)],
                )
            else:
                raise

        if not payto_res:
            raise PaymentBroadcastError("Electrum payto returned empty response.")

        if isinstance(payto_res, dict) and "hex" in payto_res:
            raw_tx = str(payto_res["hex"]).strip()
        else:
            raw_tx = str(payto_res).strip()

        if not raw_tx:
            raise PaymentBroadcastError("Electrum payto returned empty transaction hex.")

        # 2. Broadcast transaction to network
        broadcast_res = await self._call("broadcast", [raw_tx])
        if not broadcast_res:
            raise PaymentBroadcastError("Electrum broadcast returned empty response.")

        if isinstance(broadcast_res, (list, tuple)):
            if len(broadcast_res) < 2 or not broadcast_res[0]:
                detail = broadcast_res[1] if len(broadcast_res) > 1 else broadcast_res
                raise PaymentBroadcastError(f"Broadcast rejected by Electrum: {detail}")
            txid = str(broadcast_res[1]).strip()
        elif isinstance(broadcast_res, dict):
            if "error" in broadcast_res and broadcast_res["error"]:
                raise PaymentBroadcastError(f"Broadcast error: {broadcast_res['error']}")
            txid = str(broadcast_res.get("txid") or broadcast_res.get("result") or "").strip()
        else:
            txid = str(broadcast_res).strip()

        # Strictly enforce 64-character hexadecimal txid
        if len(txid) != 64 or not all(c in "0123456789abcdefABCDEF" for c in txid):
            logger.error(
                "Broadcast failed or returned invalid txid for order=%s: %r",
                order_id,
                broadcast_res,
            )
            raise PaymentBroadcastError(
                f"Electrum broadcast did not return a valid 64-character hex txid (got: {txid!r})"
            )

        logger.info("Payment broadcast successfully for order=%s: txid=%s", order_id, txid)
        print(f"[PAYMENT] Broadcast SUCCESS for order={order_id}: txid={txid}", flush=True)
        return txid
