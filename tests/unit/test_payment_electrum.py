"""Unit tests for Electrum-LTC payment adapter (mocked JSON-RPC)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from resell.adapters.payment_electrum import (
    ElectrumConnectionError,
    ElectrumPaymentAdapter,
    ElectrumRPCError,
    PaymentBroadcastError,
    format_wallet_path,
)


def test_format_wallet_path() -> None:
    """format_wallet_path replaces single backslashes with double backslashes for Windows paths."""
    # Single backslash path
    win_path = r"C:\Users\test\AppData\Roaming\Electrum-LTC\wallets\default_wallet"
    formatted = format_wallet_path(win_path)
    expected = (
        "C:\\\\Users\\\\test\\\\AppData\\\\Roaming\\\\Electrum-LTC\\\\wallets\\\\default_wallet"
    )
    assert formatted == expected

    # Already formatted / double backslash
    already_escaped = "C:\\\\Users\\\\test\\\\wallet"
    assert format_wallet_path(already_escaped) == "C:\\\\Users\\\\test\\\\wallet"

    # Forward slash / empty
    posix_path = "/home/user/.electrum/wallets/default"
    assert format_wallet_path(posix_path) == posix_path
    assert format_wallet_path("") == ""


@pytest.mark.asyncio
async def test_balance_sats_with_loaded_wallet() -> None:
    """When list_wallets finds a loaded wallet, its path is formatted and passed to getbalance."""
    wallet_raw_path = r"C:\Users\dark2\AppData\Roaming\Electrum-LTC\wallets\sellauth_resell_wallet"

    resp_wallets = MagicMock()
    resp_wallets.status_code = 200
    resp_wallets.json.return_value = {
        "jsonrpc": "2.0",
        "result": [{"path": wallet_raw_path, "synchronized": True}],
        "id": "list_wallets",
    }

    resp_balance = MagicMock()
    resp_balance.status_code = 200
    resp_balance.json.return_value = {
        "jsonrpc": "2.0",
        "result": {"confirmed": "0.00296409"},
        "id": "getbalance",
    }

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_wallets, resp_balance])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = ElectrumPaymentAdapter(
            rpc_url="http://127.0.0.1:7777",
            rpc_user="electrum",
            rpc_password="secret_password",
        )
        balance = await adapter.balance_sats()
        assert balance == 296409

        assert mock_client.post.call_count == 2

        # 1st call: list_wallets
        first_call = json.loads(mock_client.post.call_args_list[0][1]["content"])
        assert first_call["method"] == "list_wallets"

        # 2nd call: getbalance with formatted wallet path
        second_call = json.loads(mock_client.post.call_args_list[1][1]["content"])
        assert second_call["method"] == "getbalance"
        expected_wallet = (
            "C:\\\\Users\\\\dark2\\\\AppData\\\\Roaming\\\\Electrum-LTC\\\\"
            "wallets\\\\sellauth_resell_wallet"
        )
        assert second_call["params"]["wallet"] == expected_wallet


@pytest.mark.asyncio
async def test_balance_sats_decimal_string_format() -> None:
    """Parse balance when Electrum returns LTC string with decimal points."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "result": {"confirmed": "0.15000000", "unconfirmed": "0.0"},
        "id": "balance",
    }

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = ElectrumPaymentAdapter(
            rpc_url="http://127.0.0.1:7773",
            rpc_user="test_user",
            rpc_password="test_password",
        )
        balance = await adapter.balance_sats()
        # 0.15 LTC = 15,000,000 Satoshis
        assert balance == 15000000


@pytest.mark.asyncio
async def test_balance_sats_zero() -> None:
    """Parse balance when confirmed balance is zero."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "result": {"confirmed": "0", "unconfirmed": "0"},
        "id": "balance",
    }

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = ElectrumPaymentAdapter(
            rpc_url="http://127.0.0.1:7773",
            rpc_user="test_user",
            rpc_password="test_password",
        )
        balance = await adapter.balance_sats()
        assert balance == 0


@pytest.mark.asyncio
async def test_balance_sats_connection_failure() -> None:
    """Network connection failure raises ElectrumConnectionError."""
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = ElectrumPaymentAdapter(
            rpc_url="http://127.0.0.1:7773",
            rpc_user="test_user",
            rpc_password="test_password",
        )
        with pytest.raises(ElectrumConnectionError, match="Electrum daemon unreachable"):
            await adapter.balance_sats()


@pytest.mark.asyncio
async def test_balance_sats_rpc_error() -> None:
    """JSON-RPC error payload raises ElectrumRPCError."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "error": {"code": -32601, "message": "Method not found"},
        "id": "balance",
    }

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = ElectrumPaymentAdapter(
            rpc_url="http://127.0.0.1:7773",
            rpc_user="test_user",
            rpc_password="test_password",
        )
        with pytest.raises(ElectrumRPCError, match="Method not found"):
            await adapter.balance_sats()


@pytest.mark.asyncio
async def test_pay_successful_broadcast_tuple() -> None:
    """Pay constructs list_wallets, payto, and broadcast calls, returning valid txid."""
    raw_tx_hex = "0200000001abcdef12345678"
    fake_txid = "a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0"

    resp_wallets = MagicMock()
    resp_wallets.status_code = 200
    resp_wallets.json.return_value = {
        "jsonrpc": "2.0",
        "result": [{"path": r"C:\wallets\default", "synchronized": True}],
        "id": "list_wallets",
    }

    resp_payto = MagicMock()
    resp_payto.status_code = 200
    resp_payto.json.return_value = {
        "jsonrpc": "2.0",
        "result": {"hex": raw_tx_hex},
        "id": "payto",
    }

    resp_broadcast = MagicMock()
    resp_broadcast.status_code = 200
    resp_broadcast.json.return_value = {
        "jsonrpc": "2.0",
        "result": [True, fake_txid],
        "id": "broadcast",
    }

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_wallets, resp_payto, resp_broadcast])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = ElectrumPaymentAdapter(
            rpc_url="http://127.0.0.1:7773",
            rpc_user="test_user",
            rpc_password="test_password",
        )
        txid = await adapter.pay(
            address="ltc1qfakeaddress987654321",
            amount_sats=1000000,  # 0.01 LTC
            order_id=42,
        )

        assert txid == fake_txid
        assert mock_client.post.call_count == 3

        # Check list_wallets call
        wallets_body = json.loads(mock_client.post.call_args_list[0][1]["content"])
        assert wallets_body["method"] == "list_wallets"

        # Check payto payload
        payto_call_kwargs = mock_client.post.call_args_list[1][1]
        payto_body = json.loads(payto_call_kwargs["content"])
        assert payto_body["method"] == "payto"
        assert payto_body["params"]["destination"] == "ltc1qfakeaddress987654321"
        assert payto_body["params"]["amount"] == "0.01000000"
        assert payto_body["params"]["wallet"] == "C:\\\\wallets\\\\default"

        # Check broadcast payload
        broadcast_call_kwargs = mock_client.post.call_args_list[2][1]
        broadcast_body = json.loads(broadcast_call_kwargs["content"])
        assert broadcast_body["method"] == "broadcast"
        assert broadcast_body["params"][0] == raw_tx_hex


@pytest.mark.asyncio
async def test_pay_broadcast_rejected_raises_error() -> None:
    """If broadcast returns [False, reason], PaymentBroadcastError is raised."""
    raw_tx_hex = "0200000001abcdef"

    resp_wallets = MagicMock()
    resp_wallets.status_code = 200
    resp_wallets.json.return_value = {
        "jsonrpc": "2.0",
        "result": [],
        "id": "list_wallets",
    }

    resp_payto = MagicMock()
    resp_payto.status_code = 200
    resp_payto.json.return_value = {
        "jsonrpc": "2.0",
        "result": raw_tx_hex,
        "id": "payto",
    }

    resp_broadcast = MagicMock()
    resp_broadcast.status_code = 200
    resp_broadcast.json.return_value = {
        "jsonrpc": "2.0",
        "result": [False, "insufficient priority / fee too low"],
        "id": "broadcast",
    }

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_wallets, resp_payto, resp_broadcast])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        adapter = ElectrumPaymentAdapter(
            rpc_url="http://127.0.0.1:7773",
            rpc_user="test_user",
            rpc_password="test_password",
        )
        with pytest.raises(PaymentBroadcastError, match="fee too low"):
            await adapter.pay(
                address="ltc1qfakeaddress987654321",
                amount_sats=500000,
                order_id=42,
            )


@pytest.mark.asyncio
async def test_pay_invalid_arguments_raise_value_error() -> None:
    """Empty address or non-positive amount raises ValueError."""
    adapter = ElectrumPaymentAdapter(
        rpc_url="http://127.0.0.1:7773",
        rpc_user="test_user",
        rpc_password="test_password",
    )
    with pytest.raises(ValueError, match="Address cannot be empty"):
        await adapter.pay("", 1000, 1)

    with pytest.raises(ValueError, match="Amount must be positive"):
        await adapter.pay("ltc1qfake", 0, 1)

    with pytest.raises(ValueError, match="Amount must be positive"):
        await adapter.pay("ltc1qfake", -500, 1)


@pytest.mark.asyncio
async def test_pay_broadcast_empty_or_invalid_txid_raises_error() -> None:
    """Empty or non-64-char txid response from broadcast raises PaymentBroadcastError."""
    resp_wallets = MagicMock()
    resp_wallets.status_code = 200
    resp_wallets.json.return_value = {
        "jsonrpc": "2.0",
        "result": [],
        "id": "list_wallets",
    }

    resp_payto = MagicMock()
    resp_payto.status_code = 200
    resp_payto.json.return_value = {
        "jsonrpc": "2.0",
        "result": "0200000001deadbeef",
        "id": "payto",
    }

    # Case 1: Empty string result
    resp_empty = MagicMock()
    resp_empty.status_code = 200
    resp_empty.json.return_value = {"jsonrpc": "2.0", "result": "", "id": "broadcast"}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_wallets, resp_payto, resp_empty])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        adapter = ElectrumPaymentAdapter("http://127.0.0.1:7773", "u", "p")
        with pytest.raises(PaymentBroadcastError, match="empty response"):
            await adapter.pay("ltc1qfake", 100000, 1)

    # Case 2: [True, ""] (tuple with empty txid)
    resp_tuple_empty = MagicMock()
    resp_tuple_empty.status_code = 200
    resp_tuple_empty.json.return_value = {
        "jsonrpc": "2.0",
        "result": [True, ""],
        "id": "broadcast",
    }
    mock_client.post = AsyncMock(side_effect=[resp_wallets, resp_payto, resp_tuple_empty])
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        with pytest.raises(PaymentBroadcastError, match="valid 64-character hex txid"):
            await adapter.pay("ltc1qfake", 100000, 1)

    # Case 3: Invalid non-hex string
    resp_non_hex = MagicMock()
    resp_non_hex.status_code = 200
    resp_non_hex.json.return_value = {
        "jsonrpc": "2.0",
        "result": "z" * 64,
        "id": "broadcast",
    }
    mock_client.post = AsyncMock(side_effect=[resp_wallets, resp_payto, resp_non_hex])
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        with pytest.raises(PaymentBroadcastError, match="valid 64-character hex txid"):
            await adapter.pay("ltc1qfake", 100000, 1)

