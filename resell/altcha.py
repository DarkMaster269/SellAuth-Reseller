# MIT License
#
# Copyright (c) 2026 DraxonV1 / resell-sellauth contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Altcha proof-of-work challenge solver and generator."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any


class UnsolvableChallengeError(RuntimeError):
    """Raised when an Altcha challenge cannot be solved within the max number."""


def create_altcha_challenge(
    number: int,
    salt: str,
    algorithm: str = "SHA-256",
    maxnumber: int = 100000,
    signature: str = "",
) -> dict[str, Any]:
    """Generate an Altcha challenge structure for a given secret number."""
    test_str = f"{salt}{number}"
    if algorithm.upper() == "SHA-256":
        challenge_hash = hashlib.sha256(test_str.encode()).hexdigest()
    elif algorithm.upper() == "SHA-512":
        challenge_hash = hashlib.sha512(test_str.encode()).hexdigest()
    else:
        raise ValueError(f"Unsupported algorithm: {algorithm}")

    return {
        "algorithm": algorithm,
        "challenge": challenge_hash,
        "maxnumber": maxnumber,
        "salt": salt,
        "signature": signature,
    }


def solve_altcha(
    challenge: str,
    salt: str | None = None,
    algorithm: str = "SHA-256",
    maxnumber: int = 1000000,
    signature: str = "",
) -> dict[str, Any]:
    """Solve an Altcha proof-of-work challenge."""
    # Support JSON string format if salt is not passed directly
    if salt is None:
        try:
            parsed = json.loads(challenge)
            challenge_hash = parsed.get("challenge", "")
            salt_val = parsed.get("salt", "")
            algorithm = parsed.get("algorithm", "SHA-256")
            maxnumber = int(parsed.get("maxnumber", maxnumber))
            signature = parsed.get("signature", "")
        except json.JSONDecodeError as exc:
            raise ValueError("salt was not provided and challenge is not valid JSON") from exc
    else:
        challenge_hash = challenge
        salt_val = salt

    algo_upper = algorithm.upper()

    for n in range(maxnumber + 1):
        test_str = f"{salt_val}{n}"
        if algo_upper == "SHA-256":
            test_hash = hashlib.sha256(test_str.encode()).hexdigest()
        elif algo_upper == "SHA-512":
            test_hash = hashlib.sha512(test_str.encode()).hexdigest()
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

        if test_hash == challenge_hash:
            payload = {
                "algorithm": algorithm,
                "challenge": challenge_hash,
                "number": n,
                "salt": salt_val,
                "signature": signature,
            }
            b64_payload = base64.b64encode(
                json.dumps(payload, separators=(",", ":")).encode()
            ).decode()
            return {
                "number": n,
                "challenge": challenge_hash,
                "salt": salt_val,
                "signature": signature,
                "base64_payload": b64_payload,
            }

    raise UnsolvableChallengeError(
        f"Could not solve Altcha challenge within maxnumber={maxnumber}"
    )
