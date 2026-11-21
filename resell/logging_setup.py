"""Log formatting and automatic redaction filter per SECURITY.md."""

from __future__ import annotations

import logging
import re

# Regex patterns for redaction
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
LTC_BECH32_RE = re.compile(r"\b(?:ltc1|bc1)[a-z0-9]{25,60}\b", re.IGNORECASE)
LTC_LEGACY_RE = re.compile(r"\b[LM3][a-km-zA-HJ-NP-Z1-90-9]{25,35}\b")
SK_KEY_RE = re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9_-]+\b")
SIG_RE = re.compile(r"(X-Signature['\"]?\s*[:=]\s*['\"])[^'\"]+(['\"])", re.IGNORECASE)
AUTH_RE = re.compile(r"(Authorization['\"]?\s*[:=]\s*['\"])[^'\"]+(['\"])", re.IGNORECASE)
SECRET_ASSIGN_RE = re.compile(
    r"\b((?:[A-Z0-9_]*KEY|[A-Z0-9_]*SECRET|[A-Z0-9_]*TOKEN|token|secret))\s*=\s*([^\s,;]+)",
    re.IGNORECASE,
)
ITEM_CODE_RE = re.compile(
    r"((?:item_code|delivered|item_string)\s*[:=]\s*['\"])[^'\"]+(['\"])",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    """Apply masking patterns to any string to remove secrets and PII."""
    # Redact specific patterns
    text = EMAIL_RE.sub("<redacted:email>", text)
    text = LTC_BECH32_RE.sub("<redacted:ltc_address>", text)
    text = LTC_LEGACY_RE.sub("<redacted:ltc_address>", text)
    text = SK_KEY_RE.sub("<redacted:secret>", text)
    text = SIG_RE.sub(r"\1<redacted:secret>\2", text)
    text = AUTH_RE.sub(r"\1<redacted:secret>\2", text)
    text = SECRET_ASSIGN_RE.sub(r"\1=<redacted:secret>", text)
    text = ITEM_CODE_RE.sub(r"\1<redacted:item>\2", text)
    return text


class RedactingFormatter(logging.Formatter):
    """Logging formatter that intercepts messages and redacts sensitive data."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the record and apply redaction filter."""
        original_msg = super().format(record)
        return redact_text(original_msg)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger and Uvicorn loggers with RedactingFormatter globally."""
    import sys

    formatter = RedactingFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if not any(isinstance(h.formatter, RedactingFormatter) for h in root_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    # 2. Configure resell application logger
    resell_logger = logging.getLogger("resell")
    resell_logger.setLevel(level)
    if not any(isinstance(h.formatter, RedactingFormatter) for h in resell_logger.handlers):
        resell_handler = logging.StreamHandler(sys.stdout)
        resell_handler.setFormatter(formatter)
        resell_logger.addHandler(resell_handler)
        resell_logger.propagate = True

    # 3. Configure uvicorn loggers to redact secrets
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        u_logger = logging.getLogger(logger_name)
        for h in u_logger.handlers:
            if not isinstance(h.formatter, RedactingFormatter):
                h.setFormatter(formatter)


# Auto-configure on import
setup_logging()
