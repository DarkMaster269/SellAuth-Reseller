"""Unit tests for the Altcha Proof-of-Work challenge solver."""

from __future__ import annotations

import pytest

from resell.altcha import (
    UnsolvableChallengeError,
    create_altcha_challenge,
    solve_altcha,
)


def test_altcha_solver_solves_known_number() -> None:
    """Solver finds known solution number within max bounds."""
    target_number = 1234
    salt = "random_test_salt_987"
    challenge_data = create_altcha_challenge(
        number=target_number,
        salt=salt,
        algorithm="SHA-256",
        maxnumber=10000,
    )

    result = solve_altcha(
        challenge=challenge_data["challenge"],
        salt=challenge_data["salt"],
        algorithm=challenge_data["algorithm"],
        maxnumber=challenge_data["maxnumber"],
    )

    assert result["number"] == target_number
    assert result["challenge"] == challenge_data["challenge"]
    assert "base64_payload" in result


def test_altcha_unsolvable_challenge_raises_error() -> None:
    """If target number is beyond maxnumber, solver must raise UnsolvableChallengeError."""
    target_number = 50000
    challenge_data = create_altcha_challenge(
        number=target_number,
        salt="salt123",
        algorithm="SHA-256",
        maxnumber=100000,
    )

    # Search with maxnumber lower than the actual answer (search only up to 100)
    with pytest.raises(UnsolvableChallengeError):
        solve_altcha(
            challenge=challenge_data["challenge"],
            salt=challenge_data["salt"],
            algorithm=challenge_data["algorithm"],
            maxnumber=100,
        )

