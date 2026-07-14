"""Sessions service: log a session with optional payment/draw (Req 7, 15).

:func:`log_session` creates a ``sessions`` row scoped to the caller's location
with ``logged_by`` set to the acting user (Req 7.1, 15.3). The referenced player
must be within the caller's scope; a cross-scope player is rejected with a
:class:`SessionScopeError` (-> ``403``, Req 7.4), and because the scope check is
fail-closed the request is rejected rather than allowed if the scope cannot be
completed (Req 7.6).

Optional composition:

* An optional ``draw`` decrements the referenced entitlement via the
  :mod:`funhouse_api.entitlements.engine` (Req 7.3). It is performed **before**
  the session is created so a rejected draw (insufficient/inactive units) leaves
  no orphaned session; a rejected draw raises :class:`DrawRejected` (-> ``409``).
* An optional ``payment`` associates a ``payments`` record (Req 7.2), inserted
  in the same transaction as the session via the reused payments write.

The session insert, its ``sync_log`` entry (Req 7.5, 14.2), and any payment all
share one transaction; ``popia.filter_payload`` runs defensively over the
session payload (Req 14.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from funhouse_api.entitlements import engine
from funhouse_api.payments.service import insert_payment
from funhouse_pipeline.load.audit import ACTION_INSERT, append_sync_log
from funhouse_pipeline.load.popia import filter_payload


class SessionScopeError(Exception):
    """Raised when the session's player is outside the caller's scope (Req 7.4)."""


class PlayerNotFound(Exception):
    """Raised when the referenced player does not exist."""


class DrawRejected(Exception):
    """Raised when an optional entitlement draw is rejected (Req 7.3 -> 409)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PaymentInput:
    """Optional payment associated with a session (Req 7.2)."""

    amount_cents: int
    product_id: Any | None = None
    method: str | None = None
    paid_at: Any | None = None


@dataclass(frozen=True)
class DrawInput:
    """Optional entitlement draw associated with a session (Req 7.3)."""

    entitlement_id: Any
    amount: int


@dataclass(frozen=True)
class SessionResult:
    """The outcome of logging a session."""

    id: Any
    player_id: Any
    location_id: Any
    logged_by: Any
    payment_id: Any | None = None
    draw_entitlement_id: Any | None = None
    draw_remaining_units: int | None = None


def _load_player_scope(conn: Any, player_id: Any) -> tuple[Any, Any]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT location_id, school_id FROM players WHERE id = %s", (player_id,)
        )
        row = cursor.fetchone()
    if row is None:
        raise PlayerNotFound(str(player_id))
    return row[0], row[1]


def log_session(
    conn: Any,
    scope: Any,
    *,
    logged_by: Any,
    player_id: Any,
    session_type: str,
    duration_minutes: int,
    started_at: Any | None = None,
    ended_at: Any | None = None,
    school_id: Any | None = None,
    payment: PaymentInput | None = None,
    draw: DrawInput | None = None,
    now: datetime | None = None,
    location_timezone: str = "Africa/Johannesburg",
    device_id: Any | None = None,
) -> SessionResult:
    """Create a session (optionally + payment/draw) within scope (Req 7).

    Raises:
        PlayerNotFound: If the player does not exist.
        SessionScopeError: If the player is outside the caller's scope (Req 7.4).
        DrawRejected: If an optional draw is rejected (Req 7.3).
    """
    now = now or datetime.now(timezone.utc)

    # Fail-closed scope check on the referenced player (Req 7.4, 7.6).
    player_location, player_school = _load_player_scope(conn, player_id)
    from funhouse_api.rbac import AuthzError

    try:
        scope.assert_can_write(player_location, player_school)
    except AuthzError as exc:
        raise SessionScopeError(str(exc)) from exc

    # The session inherits the player's location scope; a facilitator's session
    # takes the player's school when none is supplied.
    location_id = player_location
    effective_school = school_id if school_id is not None else player_school

    # Perform the optional draw first so a rejected draw leaves no session
    # (Req 7.3). engine.draw manages its own transaction + digital signature.
    draw_entitlement_id: Any | None = None
    draw_remaining: int | None = None
    if draw is not None:
        result = engine.draw(
            conn,
            draw.entitlement_id,
            draw.amount,
            logged_by=logged_by,
            now=now,
            location_timezone=location_timezone,
            device_id=device_id,
        )
        if not result.applied:
            raise DrawRejected(result.reason or "draw rejected")
        draw_entitlement_id = result.entitlement_id
        draw_remaining = result.remaining_units

    # Defensive POPIA filter over the session payload (Req 14.1).
    payload = {
        "player_id": player_id,
        "session_type": session_type,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_minutes": duration_minutes,
        "school_id": effective_school,
        "location_id": location_id,
    }
    clean, _dropped = filter_payload(payload)

    # Session insert + audit (+ optional payment) share one transaction.
    payment_id: Any | None = None
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sessions
                    (player_id, session_type, started_at, ended_at,
                     duration_minutes, school_id, logged_by, location_id,
                     device_id, client_timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    clean["player_id"],
                    clean["session_type"],
                    clean["started_at"],
                    clean["ended_at"],
                    clean["duration_minutes"],
                    clean["school_id"],
                    logged_by,
                    clean["location_id"],
                    device_id,
                    now,
                ),
            )
            session_id = cursor.fetchone()[0]
            append_sync_log(
                cursor,
                entity="sessions",
                record_id=session_id,
                action=ACTION_INSERT,
                location_id=location_id,
                user_id=logged_by,
                device_id=device_id,
                client_timestamp=now,
            )

        if payment is not None:
            payment_id = insert_payment(
                conn,
                player_id=player_id,
                amount_cents=payment.amount_cents,
                location_id=location_id,
                logged_by=logged_by,
                product_id=payment.product_id,
                method=payment.method,
                paid_at=payment.paid_at,
                now=now,
                device_id=device_id,
            )

    conn.commit()
    return SessionResult(
        id=session_id,
        player_id=player_id,
        location_id=location_id,
        logged_by=logged_by,
        payment_id=payment_id,
        draw_entitlement_id=draw_entitlement_id,
        draw_remaining_units=draw_remaining,
    )
