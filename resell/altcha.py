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
    """Raised when an Altcha challenge cannot be solved within maxnumber."""


def solve_altcha(
    challenge: str,
    salt: str,
    algorithm: str = "SHA-256",
    maxnumber: int = 1_000_000,
    signature: str = "",
) -> dict[str, Any]:
    """Solve an Altcha proof-of-work challenge by finding the matching integer."""
    target_hash = challenge.lower().strip()
    salt_bytes = salt.encode("utf-8")

    # Brute-force proof-of-work within bounded range
    for n in range(maxnumber + 1):
        test_str = salt_bytes + str(n).encode("utf-8")
        if hashlib.sha256(test_str).hexdigest() == target_hash:
            payload = {
                "algorithm": algorithm,
                "challenge": challenge,
                "number": n,
                "salt": salt,
                "signature": signature,
            }
            json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            b64_payload = base64.b64encode(json_bytes).decode("ascii")
            return {
                "number": n,
                "challenge": challenge,
                "salt": salt,
                "signature": signature,
                "base64_payload": b64_payload,
            }

    raise UnsolvableChallengeError(
        f"Unable to solve Altcha challenge within maxnumber={maxnumber}."
    )


def create_altcha_challenge(
    number: int,
    salt: str = "testsalt",
    algorithm: str = "SHA-256",
    signature: str = "testsig",
    maxnumber: int = 100_000,
) -> dict[str, Any]:
    """Generate a synthetic Altcha challenge for testing purposes."""
    challenge_hash = hashlib.sha256((salt + str(number)).encode("utf-8")).hexdigest()
    return {
        "algorithm": algorithm,
        "challenge": challenge_hash,
        "salt": salt,
        "signature": signature,
        "maxnumber": maxnumber,
    }

