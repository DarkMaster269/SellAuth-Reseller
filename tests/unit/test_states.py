"""Unit tests for the order state machine and allowed transitions."""

from __future__ import annotations

import pytest

from resell.states import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransitionError,
    OrderState,
    validate_transition,
)


def test_allowed_transitions_table_defined() -> None:
    """Every OrderState must be present in the ALLOWED_TRANSITIONS table."""
    for state in OrderState:
        assert state in ALLOWED_TRANSITIONS


def test_valid_transitions_succeed() -> None:
    """Valid sequential transitions must succeed without error."""
    # Happy path: received -> checkout_created -> paid -> fetched -> delivered
    validate_transition(OrderState.RECEIVED, OrderState.CHECKOUT_CREATED)
    validate_transition(OrderState.CHECKOUT_CREATED, OrderState.PAID)
    validate_transition(OrderState.PAID, OrderState.FETCHED)
    validate_transition(OrderState.FETCHED, OrderState.DELIVERED)

    # Slow path: paid -> delayed -> fetched -> delivered
    validate_transition(OrderState.PAID, OrderState.DELAYED)
    validate_transition(OrderState.DELAYED, OrderState.FETCHED)

    # Fail closed paths to refund_needed
    validate_transition(OrderState.RECEIVED, OrderState.REFUND_NEEDED)
    validate_transition(OrderState.CHECKOUT_CREATED, OrderState.REFUND_NEEDED)
    validate_transition(OrderState.PAID, OrderState.REFUND_NEEDED)
    validate_transition(OrderState.DELAYED, OrderState.REFUND_NEEDED)
    validate_transition(OrderState.FETCHED, OrderState.REFUND_NEEDED)

    # Resolution of refund
    validate_transition(OrderState.REFUND_NEEDED, OrderState.REFUNDED)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (OrderState.DELIVERED, OrderState.RECEIVED),
        (OrderState.DELIVERED, OrderState.PAID),
        (OrderState.DELIVERED, OrderState.REFUND_NEEDED),
        (OrderState.REFUNDED, OrderState.RECEIVED),
        (OrderState.REFUNDED, OrderState.PAID),
        (OrderState.RECEIVED, OrderState.DELIVERED),
        (OrderState.RECEIVED, OrderState.FETCHED),
        (OrderState.CHECKOUT_CREATED, OrderState.DELIVERED),
        (OrderState.PAID, OrderState.RECEIVED),
        (OrderState.REFUND_NEEDED, OrderState.DELIVERED),
        (OrderState.REFUND_NEEDED, OrderState.PAID),
    ],
)
def test_illegal_transitions_raise_error(from_state: OrderState, to_state: OrderState) -> None:
    """Illegal transitions must raise InvalidStateTransitionError."""
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        validate_transition(from_state, to_state)

    assert str(from_state) in str(exc_info.value)
    assert str(to_state) in str(exc_info.value)
