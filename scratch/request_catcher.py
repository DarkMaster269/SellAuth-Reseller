"""FastAPI webhook catcher for Stage 0 Discovery.

Listens for incoming SellAuth webhooks or Dynamic Delivery requests,
logs all request headers, dumps the raw body, and verifies HMAC-SHA256
signatures using the X-Signature header if a secret is provided.

Usage:
    uvicorn scratch.request_catcher:app --host 0.0.0.0 --port 8000
    or
    python scratch/request_catcher.py [--port 8000] [--secret YOUR_SECRET]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

try:
    import uvicorn
    from fastapi import FastAPI, Header, Request, Response
    from fastapi.responses import JSONResponse
except ImportError:
    FastAPI = None  # type: ignore[assignment,misc]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("request_catcher")

PAYLOADS_DIR = Path("scratch") / "captured_payloads"
PAYLOADS_DIR.mkdir(parents=True, exist_ok=True)


def verify_signature(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    """Verify HMAC-SHA256 signature against the raw body."""
    if not signature_header:
        logger.warning("No X-Signature header provided in request.")
        return False

    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Handle possible hex or prefix formats
    sig_to_check = signature_header.strip()
    if sig_to_check.startswith("sha256="):
        sig_to_check = sig_to_check.split("=", 1)[1]

    matches = hmac.compare_digest(computed.lower(), sig_to_check.lower())
    if matches:
        logger.info("HMAC signature verification SUCCEEDED.")
    else:
        logger.warning(
            "HMAC signature verification FAILED. expected=%s received=%s",
            computed,
            signature_header,
        )
    return matches


def create_app(secret: str | None = None) -> Any:
    """Create and configure the FastAPI catcher app."""
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Install with `pip install fastapi uvicorn`.")

    webhook_secret = secret or os.getenv("WEBHOOK_SECRET") or os.getenv("SELLAUTH_WEBHOOK_SECRET")

    app = FastAPI(
        title="SellAuth Webhook Catcher",
        description="Stage 0 discovery catcher for capturing real SellAuth webhook payloads.",
        version="0.1.0",
    )

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def catch_all(
        request: Request,
        path: str,
        x_signature: str | None = Header(default=None, alias="X-Signature"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Response:
        """Log incoming requests, dump raw body, and return 200 immediately."""
        raw_body = await request.body()
        timestamp_str = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S_%f")

        headers_dict = dict(request.headers)
        logger.info("--- Incoming %s /%s ---", request.method, path)
        logger.info("Headers: %s", json.dumps(headers_dict, indent=2))
        logger.info("X-Signature header: %s", x_signature)
        logger.info("Idempotency-Key header: %s", idempotency_key)

        body_text = raw_body.decode("utf-8", errors="replace")
        logger.info("Raw body bytes length: %d", len(raw_body))
        logger.info("Raw body content:\n%s", body_text)

        # HMAC Verification if configured
        sig_status = "unverified"
        if webhook_secret:
            valid = verify_signature(webhook_secret, raw_body, x_signature)
            sig_status = "valid" if valid else "invalid"
        else:
            logger.info("No WEBHOOK_SECRET configured; skipping HMAC verification.")

        # Save to disk for inspection
        filename_base = f"payload_{timestamp_str}"
        meta_file = PAYLOADS_DIR / f"{filename_base}_meta.json"
        body_file = PAYLOADS_DIR / f"{filename_base}_body.json"

        meta_data = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "method": request.method,
            "path": f"/{path}",
            "headers": headers_dict,
            "signature_status": sig_status,
        }
        meta_file.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")
        body_file.write_bytes(raw_body)

        logger.info("Saved payload to: %s", body_file)
        logger.info("Saved metadata to: %s", meta_file)

        # Return fast 200 OK.
        # Include delivery format in case Dynamic Delivery is expecting product deliverable.
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "message": "Webhook received successfully",
                "delivered": "STAGE_0_PROBE_ITEM_OK",
                "signature_verified": sig_status == "valid",
            },
        )

    return app


app = create_app()


def main() -> None:
    """CLI entrypoint to run the catcher directly."""
    parser = argparse.ArgumentParser(description="SellAuth Webhook Request Catcher")
    parser.add_argument("--host", default="0.0.0.0", help="Host to listen on (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--secret", default=None, help="Webhook secret for HMAC verification")
    args = parser.parse_args()

    global app
    app = create_app(secret=args.secret)

    if uvicorn is None:
        print("Error: uvicorn is not installed. Please install uvicorn first.", file=sys.stderr)
        sys.exit(1)

    print(f"Starting webhook catcher on http://{args.host}:{args.port}")
    print("Expose this port using an HTTPS tunnel (e.g., ngrok/cloudflared/caddy).")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

