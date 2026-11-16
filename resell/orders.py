"""Orders and purchases repository managing transactional persistence."""

from __future__ import annotations

import datetime
import json
import sqlite3
from decimal import Decimal
from typing import Any

from resell.db import Database
from resell.models import (
    CreateOrderInput,
    CreatePurchaseInput,
    Order,
    OrderEvent,
    Purchase,
)
from resell.states import OrderState, validate_transition


class DuplicatePurchaseError(RuntimeError):
    """Raised when an attempt is made to create a second purchase for an order."""


class OrdersRepo:
    """Thread-safe transactional repository for orders, purchases, and state transitions."""

    def __init__(self, db: Database) -> None:
        """Initialize repository with a Database instance."""
        self.db = db

    def _row_to_order(self, row: sqlite3.Row) -> Order:
        """Convert a database row into an Order domain model."""
        return Order(
            id=row["id"],
            invoice_id=row["invoice_id"],
            item_id=row["item_id"],
            product_id=row["product_id"],
            variant_id=row["variant_id"],
            quantity=row["quantity"],
            email=row["email"],
            price_paid_usd=Decimal(row["price_paid_usd"]),
            currency=row["currency"],
            state=OrderState(row["state"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_purchase(self, row: sqlite3.Row) -> Purchase:
        """Convert a database row into a Purchase domain model."""
        items_raw = row["items_delivered"]
        items = json.loads(items_raw) if items_raw else None
        return Purchase(
            id=row["id"],
            order_id=row["order_id"],
            supplier_domain=row["supplier_domain"],
            supplier_product_slug=row["supplier_product_slug"],
            supplier_variant_id=row["supplier_variant_id"],
            supplier_checkout_id=row["supplier_checkout_id"],
            ltc_address=row["ltc_address"],
            ltc_amount_sats=row["ltc_amount_sats"],
            txid=row["txid"],
            items_delivered=items,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_order_if_new(self, input_data: CreateOrderInput) -> tuple[Order, bool]:
        """Create order idempotently. Returns (Order, created_flag)."""
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()

        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            try:
                # 1. Check if invoice already exists
                cursor.execute(
                    "SELECT * FROM orders WHERE invoice_id = ?;",
                    (input_data.invoice_id,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    cursor.execute("COMMIT;")
                    return (self._row_to_order(existing), False)

                # 2. Insert new order
                cursor.execute(
                    """
                    INSERT INTO orders (
                        invoice_id, item_id, product_id, variant_id, quantity,
                        email, price_paid_usd, currency, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        input_data.invoice_id,
                        input_data.item_id,
                        input_data.product_id,
                        input_data.variant_id,
                        input_data.quantity,
                        input_data.email,
                        str(input_data.price_paid_usd),
                        input_data.currency,
                        OrderState.RECEIVED.value,
                        now_utc,
                        now_utc,
                    ),
                )
                order_id = cursor.lastrowid
                assert order_id is not None

                # 3. Insert initial event
                cursor.execute(
                    """
                    INSERT INTO events (
                        order_id, from_state, to_state, reason, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        order_id,
                        None,
                        OrderState.RECEIVED.value,
                        "Order received via webhook",
                        None,
                        now_utc,
                    ),
                )

                cursor.execute("COMMIT;")

                # Fetch created order
                cursor.execute("SELECT * FROM orders WHERE id = ?;", (order_id,))
                row = cursor.fetchone()
                return (self._row_to_order(row), True)

            except sqlite3.IntegrityError:
                # Handled race condition: another thread inserted between SELECT and INSERT
                cursor.execute("ROLLBACK;")
                cursor.execute(
                    "SELECT * FROM orders WHERE invoice_id = ?;",
                    (input_data.invoice_id,),
                )
                row = cursor.fetchone()
                return (self._row_to_order(row), False)
            except Exception:
                cursor.execute("ROLLBACK;")
                raise
            finally:
                cursor.close()

    def get_order(self, order_id: int) -> Order | None:
        """Fetch order by internal primary key ID."""
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE id = ?;", (order_id,))
            row = cursor.fetchone()
            cursor.close()
            return self._row_to_order(row) if row else None

    def get_order_by_invoice(self, invoice_id: int) -> Order | None:
        """Fetch order by SellAuth invoice ID."""
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE invoice_id = ?;", (invoice_id,))
            row = cursor.fetchone()
            cursor.close()
            return self._row_to_order(row) if row else None

    def get_all_orders(self) -> list[Order]:
        """Fetch all orders ordered by ID."""
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders ORDER BY id ASC;")
            rows = cursor.fetchall()
            cursor.close()
            return [self._row_to_order(r) for r in rows]

    def transition(
        self,
        order_id: int,
        to_state: OrderState,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Order:
        """Atomically transition order state and record event log."""
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        meta_json = json.dumps(metadata) if metadata is not None else None

        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            try:
                cursor.execute("SELECT * FROM orders WHERE id = ?;", (order_id,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Order {order_id} not found.")

                current_state = OrderState(row["state"])
                validate_transition(current_state, to_state)

                cursor.execute(
                    "UPDATE orders SET state = ?, updated_at = ? WHERE id = ?;",
                    (to_state.value, now_utc, order_id),
                )

                cursor.execute(
                    """
                    INSERT INTO events (
                        order_id, from_state, to_state, reason, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (order_id, current_state.value, to_state.value, reason, meta_json, now_utc),
                )

                cursor.execute("COMMIT;")

                cursor.execute("SELECT * FROM orders WHERE id = ?;", (order_id,))
                updated_row = cursor.fetchone()
                return self._row_to_order(updated_row)

            except Exception:
                cursor.execute("ROLLBACK;")
                raise
            finally:
                cursor.close()

    def get_events(self, order_id: int) -> list[OrderEvent]:
        """Fetch all event audit entries for an order."""
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM events WHERE order_id = ? ORDER BY id ASC;",
                (order_id,),
            )
            rows = cursor.fetchall()
            cursor.close()

            events = []
            for r in rows:
                meta = json.loads(r["metadata"]) if r["metadata"] else None
                from_st = OrderState(r["from_state"]) if r["from_state"] else None
                events.append(
                    OrderEvent(
                        id=r["id"],
                        order_id=r["order_id"],
                        from_state=from_st,
                        to_state=OrderState(r["to_state"]),
                        reason=r["reason"],
                        metadata=meta,
                        created_at=r["created_at"],
                    )
                )
            return events

    def create_purchase(self, input_data: CreatePurchaseInput) -> Purchase:
        """Create a supplier purchase record. Raises DuplicatePurchaseError if already exists."""
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        items_json = (
            json.dumps(input_data.items_delivered)
            if input_data.items_delivered is not None
            else None
        )

        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            try:
                # Strict guarantee: check for existing purchase
                cursor.execute(
                    "SELECT id FROM purchases WHERE order_id = ?;",
                    (input_data.order_id,),
                )
                if cursor.fetchone() is not None:
                    raise DuplicatePurchaseError(
                        f"Purchase already exists for order_id={input_data.order_id}."
                    )

                cursor.execute(
                    """
                    INSERT INTO purchases (
                        order_id, supplier_domain, supplier_product_slug, supplier_variant_id,
                        supplier_checkout_id, ltc_address, ltc_amount_sats, txid,
                        items_delivered, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        input_data.order_id,
                        input_data.supplier_domain,
                        input_data.supplier_product_slug,
                        input_data.supplier_variant_id,
                        input_data.supplier_checkout_id,
                        input_data.ltc_address,
                        input_data.ltc_amount_sats,
                        input_data.txid,
                        items_json,
                        now_utc,
                        now_utc,
                    ),
                )
                purchase_id = cursor.lastrowid
                cursor.execute("COMMIT;")

                cursor.execute("SELECT * FROM purchases WHERE id = ?;", (purchase_id,))
                row = cursor.fetchone()
                return self._row_to_purchase(row)

            except sqlite3.IntegrityError as err:
                cursor.execute("ROLLBACK;")
                raise DuplicatePurchaseError(
                    f"Duplicate purchase for order_id={input_data.order_id}: {err}"
                ) from err
            except Exception:
                cursor.execute("ROLLBACK;")
                raise
            finally:
                cursor.close()

    def get_purchase(self, order_id: int) -> Purchase | None:
        """Fetch purchase by order ID."""
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM purchases WHERE order_id = ?;", (order_id,))
            row = cursor.fetchone()
            cursor.close()
            return self._row_to_purchase(row) if row else None

    def update_purchase(self, order_id: int, **fields: Any) -> Purchase:
        """Update fields on an existing purchase."""
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        fields["updated_at"] = now_utc

        if "items_delivered" in fields and isinstance(fields["items_delivered"], list):
            fields["items_delivered"] = json.dumps(fields["items_delivered"])

        set_clauses = [f"{k} = ?" for k in fields.keys()]
        values = list(fields.values()) + [order_id]

        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            try:
                cursor.execute(
                    f"UPDATE purchases SET {', '.join(set_clauses)} WHERE order_id = ?;",
                    values,
                )
                cursor.execute("COMMIT;")

                cursor.execute("SELECT * FROM purchases WHERE order_id = ?;", (order_id,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Purchase for order_id={order_id} not found.")
                return self._row_to_purchase(row)
            except Exception:
                cursor.execute("ROLLBACK;")
                raise
            finally:
                cursor.close()

    def orders_to_resume(self) -> list[Order]:
        """Fetch all active orders in non-terminal states."""
        terminal_states = (OrderState.DELIVERED.value, OrderState.REFUNDED.value)
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT * FROM orders
                WHERE state NOT IN ({', '.join('?' for _ in terminal_states)})
                ORDER BY id ASC;
                """,
                terminal_states,
            )
            rows = cursor.fetchall()
            cursor.close()
            return [self._row_to_order(r) for r in rows]
