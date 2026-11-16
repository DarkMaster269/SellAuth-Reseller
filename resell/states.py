"""Order lifecycle states and allowed transitions table."""

from __future__ import annotations

from enum import StrEnum


class OrderState(StrEnum):
    """Enumeration of all valid lifecycle states for an order."""

    RECEIVED = "received"
    CHECKOUT_CREATED = "checkout_created"
    PAID = "paid"
    DELAYED = "delayed"
    FETCHED = "fetched"
    DELIVERED = "delivered"
    REFUND_NEEDED = "refund_needed"
    REFUNDED = "refunded"

    def __str__(self) -> str:
        return self.value


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal order state transition is attempted."""

    def __init__(self, from_state: OrderState, to_state: OrderState) -> None:
        super().__init__(
            f"Illegal transition from '{from_state.value}' to '{to_state.value}'."
        )
        self.from_state = from_state
        self.to_state = to_state


ALLOWED_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.RECEIVED: {
        OrderState.CHECKOUT_CREATED,
        OrderState.REFUND_NEEDED,
    },
    OrderState.CHECKOUT_CREATED: {
        OrderState.PAID,
        OrderState.REFUND_NEEDED,
    },
    OrderState.PAID: {
        OrderState.DELAYED,
        OrderState.FETCHED,
        OrderState.REFUND_NEEDED,
    },
    OrderState.DELAYED: {
        OrderState.FETCHED,
        OrderState.REFUND_NEEDED,
    },
    OrderState.FETCHED: {
        OrderState.DELIVERED,
        OrderState.REFUND_NEEDED,
    },
    OrderState.DELIVERED: set(),
    OrderState.REFUND_NEEDED: {
        OrderState.REFUNDED,
    },
    OrderState.REFUNDED: set(),
}


def validate_transition(from_state: OrderState, to_state: OrderState) -> None:
    """Validate that transition from from_state to to_state is permissible."""
    allowed = ALLOWED_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise InvalidStateTransitionError(from_state, to_state)
