"""Payments service: record payments and read products (Req 10).

* :func:`insert_payment` is the low-level, reusable write: it runs
  ``popia.filter_payload`` defensively (Req 14.1), inserts a ``payments`` row
  with the amount as integer cents and ``logged_by`` set to the acting user
  (Req 10.1), optionally associates a product (Req 10.2), and appends a
  ``sync_log`` entry in the **same** transaction as the write (Req 10.4, 14.2).
  It does not commit, so it composes inside a larger write (e.g. a session log).
* :func:`record_payment` is the endpoint-level operation: it loads the player's
  scope, rejects a cross-scope write (Req 3.5), calls :func:`insert_payment`,
  and commits.
* :func:`list_products` returns the seeded catalog within the caller's scope
  (Req 10.3). Products carry ``location_id`` but are not school-associated, so
  they are scoped by location only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from funhouse_pipeline.load.audit import ACTION_INSERT, append_sync_log
from funhouse_pipeline.load.popia import filter_payload


class PaymentScopeError(Exception):
    """Raised when a payment targets a player outside the caller's scope (Req 3.5)."""


class PlayerNotFound(Exception):
    """Raised when the referenced player does not exist."""


def _load_player_scope(conn: Any, player_id: Any) -> tuple[Any, Any]:
    """Return ``(location_id, school_id)`` for a player, or raise if absent."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT location_id, school_id FROM players WHERE id = %s", (player_id,)
        )
        row = cursor.fetchone()
    if row is None:
        raise PlayerNotFound(str(player_id))
    return row[0], row[1]


def insert_payment(
    conn: Any,
    *,
    player_id: Any,
    amount_cents: int,
    location_id: Any,
    logged_by: Any,
    product_id: Any | None = None,
    method: str | None = None,
    paid_at: Any | None = None,
    now: datetime | None = None,
    device_id: Any | None = None,
) -> Any:
    """Insert a ``payments`` row and audit it in one transaction (Req 10.1, 10.4).

    Does not commit; the caller owns the outer transaction. Returns the new
    ``payments.id``.
    """
    now = now or datetime.now(timezone.utc)
    payload = {
        "player_id": player_id,
        "product_id": product_id,
        "amount_cents": int(amount_cents),
        "method": method,
        "paid_at": paid_at,
        "location_id": location_id,
    }
    clean, _dropped = filter_payload(payload)

    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO payments
                    (player_id, product_id, amount_cents, method, paid_at,
                     logged_by, location_id, device_id, client_timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    clean["player_id"],
                    clean["product_id"],
                    clean["amount_cents"],
                    clean["method"],
                    clean["paid_at"],
                    logged_by,
                    clean["location_id"],
                    device_id,
                    now,
                ),
            )
            payment_id = cursor.fetchone()[0]
            append_sync_log(
                cursor,
                entity="payments",
                record_id=payment_id,
                action=ACTION_INSERT,
                location_id=location_id,
                user_id=logged_by,
                device_id=device_id,
                client_timestamp=now,
            )
    return payment_id


@dataclass(frozen=True)
class PaymentOut:
    """A recorded payment."""

    id: Any
    player_id: Any
    product_id: Any | None
    amount_cents: int
    method: str | None
    location_id: Any


def record_payment(
    conn: Any,
    scope: Any,
    *,
    logged_by: Any,
    player_id: Any,
    amount_cents: int,
    product_id: Any | None = None,
    method: str | None = None,
    paid_at: Any | None = None,
    now: datetime | None = None,
    device_id: Any | None = None,
) -> PaymentOut:
    """Record a payment against a player within scope (Req 10.1, 10.2, 10.4).

    Raises:
        PlayerNotFound: If the player does not exist.
        PaymentScopeError: If the player is outside the caller's scope (Req 3.5).
    """
    location_id, school_id = _load_player_scope(conn, player_id)

    if not getattr(scope, "unrestricted", False):
        # Reuse the RBAC write assertion semantics (fail-closed, Req 3.5).
        from funhouse_api.rbac import AuthzError

        try:
            scope.assert_can_write(location_id, school_id)
        except AuthzError as exc:
            raise PaymentScopeError(str(exc)) from exc

    payment_id = insert_payment(
        conn,
        player_id=player_id,
        amount_cents=amount_cents,
        location_id=location_id,
        logged_by=logged_by,
        product_id=product_id,
        method=method,
        paid_at=paid_at,
        now=now,
        device_id=device_id,
    )
    conn.commit()
    return PaymentOut(
        id=payment_id,
        player_id=player_id,
        product_id=product_id,
        amount_cents=int(amount_cents),
        method=method,
        location_id=location_id,
    )


@dataclass(frozen=True)
class ProductOut:
    """A seeded product within scope (Req 10.3)."""

    id: Any
    name: str
    type: str
    price_cents: int
    rules: dict[str, Any]
    location_id: Any


def list_products(conn: Any, scope: Any) -> list[ProductOut]:
    """Return the seeded product catalog within scope (Req 10.3).

    Products carry ``location_id`` but are not school-associated, so they are
    scoped by location only (a facilitator sees its location's products).
    """
    conditions: list[str] = []
    params: list[Any] = []
    if not getattr(scope, "unrestricted", False):
        conditions.append("location_id = %s")
        params.append(scope.location_id)
    where = " AND ".join(conditions) if conditions else "TRUE"
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT id, name, type, price_cents, rules, location_id "
            f"FROM products WHERE {where} ORDER BY name",
            params,
        )
        rows = cursor.fetchall()
    return [
        ProductOut(
            id=r[0],
            name=r[1],
            type=r[2],
            price_cents=r[3],
            rules=dict(r[4]) if isinstance(r[4], dict) else (r[4] or {}),
            location_id=r[5],
        )
        for r in rows
    ]
