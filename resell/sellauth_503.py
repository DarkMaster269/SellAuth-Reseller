"""SellAuth 503 JavaScript Proof-of-Work challenge solver."""

from __future__ import annotations

import hashlib
import re


def solve_sellauth_503(html_content: str) -> str:
    """Parse SellAuth's obfuscated 503 JavaScript challenge and solve the SHA-1 PoW.

    Returns the full cookie string (e.g., 'yX3=6C8210CEF92BD6ADB225A59FC2647DC78E6813A50').
    """
    # 1. Extract the array
    array_match = re.search(r"const\s+a0_0x2a54\s*=\s*\[(.*?)\];", html_content)
    if not array_match:
        raise ValueError("Could not find obfuscated array a0_0x2a54 in HTML.")

    array_str = array_match.group(1)
    # Parse the array elements (strings in single quotes)
    items = re.findall(r"'([^']*)'", array_str)
    if len(items) < 3:
        raise ValueError("Obfuscated array does not have enough elements.")

    # 2. Extract the shift value from the IIFE: (function(..., ...){...}(a0_0x2a54, 0x178))
    shift_match = re.search(r"\(a0_0x2a54,\s*(0x[0-9a-fA-F]+)\)", html_content)
    if not shift_match:
        raise ValueError("Could not find array shift value in HTML.")
    shift_hex = shift_match.group(1)
    shift_count = int(shift_hex, 16)

    # Apply the JS shift logic (equivalent to JS push(shift()) loop)
    actual_shift = shift_count % len(items)
    items = items[actual_shift:] + items[:actual_shift]

    # Now, items[0] is the cookie name (e.g., 'yX3=')
    # items[2] is the challenge string
    cookie_name = items[0].replace("=", "")
    challenge = items[2]

    # In JS: let n1 = parseInt('0x' + c[0x0]);
    try:
        n1 = int(challenge[0], 16)
    except ValueError as exc:
        raise ValueError("Could not parse n1 index from challenge string.") from exc

    target_byte_1 = 0xB0
    target_byte_2 = 0x0B

    i = 0
    while True:
        test_str = f"{challenge}{i}"
        digest_bytes = hashlib.sha1(test_str.encode()).digest()

        if len(digest_bytes) > n1 + 1:
            if digest_bytes[n1] == target_byte_1 and digest_bytes[n1 + 1] == target_byte_2:
                cookie_value = f"{challenge}{i}"
                return f"{cookie_name}={cookie_value}"

        i += 1
        if i > 10_000_000:
            raise RuntimeError("Failed to solve 503 challenge within 10 million attempts.")
