"""Alerts_Engine: deterministic rule-based operational alerts (Req 12).

:func:`alerts` evaluates four rules using **pure conditional logic in SQL** and
issues no AI or model call (Req 12.1), so computing alerts twice over the same
data yields identical results (Property 21). Each rule is restricted to the
caller's scope for a manager/facilitator (Req 12.6).

The rules and their exact boundaries (all evaluated against the supplied
``now``, so the engine is deterministic and testable):

* **no-recent-session** (Req 12.2) -- a player in scope with no session whose
  time (``COALESCE(started_at, created_at)``) is on or after ``now - 7 days``.
  A player with no sessions at all qualifies. Boundary: a session exactly
  ``7 days`` before ``now`` counts as recent (no alert).
* **entitlement-expiring** (Req 12.3) -- an ``active`` entitlement whose
  ``valid_to`` falls within the horizon: ``now_date <= valid_to <=
  now_date + ALERT_EXPIRY_HORIZON_DAYS``. Boundary: ``valid_to`` exactly on the
  horizon day is included; one day past it is not.
* **subscription-due** (Req 12.4) -- an ``active`` entitlement under a
  ``subscription`` product whose ``valid_to`` (term end) is on or before
  ``now_date`` (the term has reached its end -> renewal due).
* **unsynced-device** (Req 12.5) -- a device whose most recent
  ``sync_log.server_timestamp`` is strictly older than ``now - 5 days``.

Alert subjects are returned as strings (``subject_id``): a player/entitlement
id, or a device id (``sync_log.device_id`` is free text, not a UUID).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# Rule constants (Req 12.2, 12.5). The entitlement-expiring horizon is
# configurable via ApiConfig.alert_expiry_horizon_days (Req 12.3).
NO_RECENT_SESSION_DAYS = 7
UNSYNCED_DEVICE_DAYS = 5

# Alert type identifiers (stable, machine-readable).
ALERT_NO_RECENT_SESSION = "no_recent_session"
ALERT_ENTITLEMENT_EXPIRING = "entitlement_expiring"
ALERT_SUBSCRIPTION_DUE = "subscription_due"
ALERT_UNSYNCED_DEVICE = "unsynced_device"


@dataclass(frozen=True)
class Alert:
    """A single deterministic alert (Req 12)."""

    type: str
    subject_id: str
    detail: str = ""


def _scope_sql(
    scope: Any,
    *,
    location_col: str,
    school_col: str | None,
) -> tuple[list[str], list[Any]]:
    """Return scope ``WHERE`` conditions for a table alias (Req 12.6)."""
    if getattr(scope, "unrestricted", False):
        return [], []
    conditions = [f"{location_col} = %s"]
    params: list[Any] = [scope.location_id]
    if getattr(scope, "role", None) == "facilitator" and school_col is not None:
        conditions.append(f"{school_col} = %s")
        params.append(scope.school_id)
    return conditions, params


def alerts(
    conn: Any,
    scope: Any,
    *,
    now: datetime | None = None,
    expiry_horizon_days: int = 7,
) -> list[Alert]:
    """Compute all deterministic alerts within scope (Req 12).

    Args:
        conn: An open psycopg connection.
        scope: The caller's :class:`~funhouse_api.rbac.Scope`.
        now: Evaluation time (defaults to now, UTC); all boundaries are relative
            to it, so the result is a pure function of the data and ``now``.
        expiry_horizon_days: The entitlement-expiring horizon (Req 12.3).

    Returns:
        A deterministically ordered list of :class:`Alert`.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_date = now.date()
    session_cutoff = now - timedelta(days=NO_RECENT_SESSION_DAYS)
    device_cutoff = now - timedelta(days=UNSYNCED_DEVICE_DAYS)
    horizon_date = now_date + timedelta(days=expiry_horizon_days)

    results: list[Alert] = []

    # --- Rule 1: no-recent-session (Req 12.2) ------------------------------- #
    p_conditions, p_params = _scope_sql(
        scope, location_col="p.location_id", school_col="p.school_id"
    )
    p_where = " AND ".join(["p.active", *p_conditions]) if p_conditions else "p.active"
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT p.id
            FROM players p
            WHERE {p_where}
              AND NOT EXISTS (
                  SELECT 1 FROM sessions s
                  WHERE s.player_id = p.id
                    AND COALESCE(s.started_at, s.created_at) >= %s
              )
            ORDER BY p.id
            """,
            [*p_params, session_cutoff],
        )
        for row in cursor.fetchall():
            results.append(
                Alert(ALERT_NO_RECENT_SESSION, str(row[0]), "no session in 7 days")
            )

    # --- Rule 2: entitlement-expiring (Req 12.3) ---------------------------- #
    e_conditions, e_params = _scope_sql(
        scope, location_col="e.location_id", school_col="pl.school_id"
    )
    e_where = " AND ".join(
        [
            "e.status = 'active'",
            "e.valid_to IS NOT NULL",
            "e.valid_to >= %s",
            "e.valid_to <= %s",
            *e_conditions,
        ]
    )
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT e.id
            FROM entitlements e
            JOIN players pl ON pl.id = e.player_id
            WHERE {e_where}
            ORDER BY e.id
            """,
            [now_date, horizon_date, *e_params],
        )
        for row in cursor.fetchall():
            results.append(
                Alert(
                    ALERT_ENTITLEMENT_EXPIRING,
                    str(row[0]),
                    "entitlement expiring within horizon",
                )
            )

    # --- Rule 3: subscription-due (Req 12.4) -------------------------------- #
    sub_conditions, sub_params = _scope_sql(
        scope, location_col="e.location_id", school_col="pl.school_id"
    )
    sub_where = " AND ".join(
        [
            "e.status = 'active'",
            "pr.type = 'subscription'",
            "e.valid_to IS NOT NULL",
            "e.valid_to <= %s",
            *sub_conditions,
        ]
    )
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT e.id
            FROM entitlements e
            JOIN products pr ON pr.id = e.product_id
            JOIN players pl ON pl.id = e.player_id
            WHERE {sub_where}
            ORDER BY e.id
            """,
            [now_date, *sub_params],
        )
        for row in cursor.fetchall():
            results.append(
                Alert(ALERT_SUBSCRIPTION_DUE, str(row[0]), "subscription renewal due")
            )

    # --- Rule 4: unsynced-device (Req 12.5) --------------------------------- #
    # Devices are not school-associated; scope by location only.
    d_conditions, d_params = _scope_sql(
        scope, location_col="location_id", school_col=None
    )
    d_where = " AND ".join(["device_id IS NOT NULL", *d_conditions])
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT device_id, MAX(server_timestamp) AS last_sync
            FROM sync_log
            WHERE {d_where}
            GROUP BY device_id
            HAVING MAX(server_timestamp) < %s
            ORDER BY device_id
            """,
            [*d_params, device_cutoff],
        )
        for row in cursor.fetchall():
            results.append(
                Alert(ALERT_UNSYNCED_DEVICE, str(row[0]), "device last sync > 5 days")
            )

    return results
