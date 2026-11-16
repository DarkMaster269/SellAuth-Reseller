"""Unit tests for log redaction filter per SECURITY.md."""

from __future__ import annotations

import io
import logging

from resell.logging_setup import RedactingFormatter


def test_redacts_sensitive_data() -> None:
    """Formatter must redact secrets, emails, LTC addresses, and items."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    formatter = RedactingFormatter("%(levelname)s: %(message)s")
    handler.setFormatter(formatter)

    logger = logging.getLogger("test_redaction")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # 1. Email redaction
    logger.info("Buyer email is customer@example.com for order 123")
    # 2. Litecoin address redaction (bech32 ltc1... or legacy L... / M...)
    logger.info("Sending payment to ltc1q269fakeltcaddress9876543210abc for purchase")
    logger.info("Legacy address is LZsFakeAddress1234567890abcdefghij")
    # 3. Secret and token redaction
    logger.info("Loaded SELLAUTH_API_KEY=sk_test_SECRETKEY123456 and token=abcTOKEN123")
    logger.info(
        "Headers: {'X-Signature': 'abcdef0123456789abcdef0123456789', "
        "'Authorization': 'Bearer sec_token'}"
    )
    # 4. Item code redaction
    logger.info("Supplier delivered: item_code='DISCORD-NITRO-GIFT-CODE-1234'")

    output = stream.getvalue()

    # Assert real sensitive data is not leaked in output
    assert "customer@example.com" not in output
    assert "ltc1q269fakeltcaddress9876543210abc" not in output
    assert "LZsFakeAddress1234567890abcdefghij" not in output
    assert "sk_test_SECRETKEY123456" not in output
    assert "DISCORD-NITRO-GIFT-CODE-1234" not in output

    # Assert redacted replacements are present
    assert "<redacted:email>" in output
    assert "<redacted:ltc_address>" in output
    assert "<redacted:secret>" in output
    assert "<redacted:item>" in output
