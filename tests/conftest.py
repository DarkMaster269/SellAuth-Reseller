"""Shared test fixtures, markers, and socket blocking per TESTING.md."""

from __future__ import annotations

import os

import pytest

try:
    import pytest_socket
except ImportError:
    pytest_socket = None


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add CLI flags for opt-in live tests."""
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run live tests that hit real external websites (read-only)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "live: marks tests that touch real supplier websites (read-only, opt-in)",
    )
    config.addinivalue_line(
        "markers",
        "money: marks tests that spend real Litecoin (requires ALLOW_MONEY_TESTS=1)",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Enforce network blocking and money test gates."""
    # Check money marker
    money_marker = item.get_closest_marker("money")
    if money_marker is not None:
        if os.getenv("ALLOW_MONEY_TESTS") != "1":
            pytest.skip("Money tests require ALLOW_MONEY_TESTS=1 environment variable.")

    # Check live marker
    live_marker = item.get_closest_marker("live")
    if live_marker is not None:
        if not item.config.getoption("--run-live"):
            pytest.skip("Live tests require --run-live CLI flag.")

    # Block external internet sockets in unit, contract, and chaos suites
    path_str = str(item.fspath) if hasattr(item, "fspath") else str(item.path)
    is_sandboxed = (
        "unit" in path_str
        or "contract" in path_str
        or "chaos" in path_str
    )
    if is_sandboxed and pytest_socket is not None:
        # Allow loopback for Windows asyncio internal socketpair/pipe, but block all internet
        pytest_socket.disable_socket(allow_unix_socket=True)
        pytest_socket.enable_socket()
        pytest_socket.socket_allow_hosts(["127.0.0.1", "localhost", "::1"])


def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Re-enable sockets if disabled."""
    if pytest_socket is not None:
        pytest_socket.enable_socket()
