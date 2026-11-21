"""Unit tests for SellAuth obfuscated 503 JavaScript PoW challenge solver."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from resell.sellauth_503 import solve_sellauth_503

FIXTURE_503_PATH = (
    Path(__file__).parent.parent / "fixtures" / "supplier_pages" / "503_challenge.html"
)


def test_solve_sellauth_503_obfuscated_structure() -> None:
    """Solver parses obfuscated array, applies rotation shift, and solves SHA-1 PoW."""
    # Array before shift:
    # We want after shift: items[0] == 'yX3=', items[2] == '0abcdef'
    # Length = 4. With shift = 1: items[1:] + items[:1]
    # So initial items: ['prev', 'yX3=', 'dummy', '0abcdef']
    # shifted by 1: ['yX3=', 'dummy', '0abcdef', 'prev']
    # items[0] = 'yX3=', items[2] = '0abcdef'
    fake_html = """
    <!DOCTYPE html>
    <html>
    <head><title>503 Service Temporarily Unavailable</title></head>
    <body>
    <script>
        const a0_0x2a54 = ['prev', 'yX3=', 'dummy', '0abcdef'];
        (function(arr, shift) {
            // rotation logic
        }(a0_0x2a54, 0x1));
    </script>
    </body>
    </html>
    """
    cookie_str = solve_sellauth_503(fake_html)
    assert cookie_str.startswith("yX3=")

    cookie_name, cookie_val = cookie_str.split("=", 1)
    assert cookie_name == "yX3"
    assert cookie_val.startswith("0abcdef")

    # Verify PoW condition: digest[n1] == 0xB0 and digest[n1 + 1] == 0x0B
    # where n1 = int('0', 16) = 0
    digest = hashlib.sha1(cookie_val.encode()).digest()
    assert digest[0] == 0xB0
    assert digest[1] == 0x0B


def test_solve_sellauth_503_missing_array_raises_value_error() -> None:
    """HTML without obfuscated array raises ValueError."""
    fake_html = "<html><body><h1>503 Service Unavailable</h1></body></html>"
    with pytest.raises(ValueError, match="Could not find obfuscated array"):
        solve_sellauth_503(fake_html)


def test_solve_sellauth_503_missing_shift_raises_value_error() -> None:
    """HTML without shift IIFE raises ValueError."""
    fake_html = "<script>const a0_0x2a54 = ['a', 'b', 'c'];</script>"
    with pytest.raises(ValueError, match="Could not find array shift value"):
        solve_sellauth_503(fake_html)


@pytest.mark.skipif(
    not FIXTURE_503_PATH.is_file(),
    reason="503_challenge.html fixture not yet saved",
)
def test_solve_sellauth_503_real_fixture() -> None:
    """If real 503 fixture is present, solve and verify exact yX3= cookie format."""
    html = FIXTURE_503_PATH.read_text(encoding="utf-8")
    cookie_str = solve_sellauth_503(html)
    assert cookie_str.startswith("yX3=")
    assert "=" in cookie_str
