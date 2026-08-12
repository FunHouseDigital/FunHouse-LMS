"""Entitlement_Engine: create, draw, recurring reset, balance (Req 8, 9).

This is the deterministic accounting core of the FunHouse Container API. It
issues **no AI or model call** (Req 9.4): every entitlement's units and validity
window are a pure function of the seeded ``products.rules`` JSONB and the current
time. Four operations live here:

* :func:`create_entitlement` -- create an ``entitlements`` row whose
  ``remaining_units`` and ``valid_from``/``valid_to`` are derived from the
  product rules; the write runs ``popia.filter_payload`` first and appends a
  ``sync_log`` entry in the same transaction (Req 8.1, 14.1, 14.2).
* :func:`draw` -- the accountable decrement: lock the row ``FOR UPDATE``, apply a
  recurring reset first if due, reject an inactive or under-funded draw leaving
  units unchanged, otherwise decrement and append the **Digital_Signature** (a
  ``sync_log`` ``update`` entry recording the acting user + server timestamp) in
  the *same* transaction. If the signature cannot be recorded the decrement is
  rolled back (Req 8.2-8.5, 8.8, 8.9).
* :func:`reset_if_new_period` -- a pure function computing whether a recurring
  entitlement has crossed into a new period and, if so, the reset allowance and
  new period start (Req 9).
* :func:`balance` -- the scoped balance query for a player (Req 8.6).

Units convention
----------------
``remaining_units`` are stored as **integer minutes**. A product rule expressed
in hours (``hours_per_week`` / ``hours``) is converted to minutes
(``hours * 60``); e.g. Holiday Special ``hours_per_week: 3`` -> ``180`` units.
Rules that already express discrete counts (``units`` / ``sessions``) are used
verbatim. A product with none of these rules yields ``NULL`` remaining units
(an unlimited entitlement, never decremented).

Period boundary convention
---------------------------
For a recurring product the period is **weekly**, anchored on the weekday named
by ``rules.reset`` (e.g. ``"sunday"``). The period start is the most recent
occurrence of that weekday at 00:00 in the configured location timezone,
expressed as a ``DATE`` and stored in the reused ``entitlements.valid_from``
column (no schema change). A product that carries a weekly allowance
(``hours_per_week``) but names no ``reset`` day defaults to a Monday anchor. The
boundary is a pure, repeatable function of the rules and the current time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from funhouse_api.config import DEFAULT_LOCATION_TIMEZONE
from funhouse_pipeline.load.audit import (
    ACTION_INSERT,
    ACTION_SKIP,
    ACTION_UPDATE,
    append_sync_log,
)
from funhouse_pipeline.load.popia import filter_payload

MINUTES_PER_HOUR = 60

STATUS_ACTIVE = "active"

# Draw outcome statuses.
DRAW_APPLIED = "applied"
DRAW_SKIPPED = "skipped"
DRAW_REJECTED = "rejected"

# Rejection reasons (stable, machine-readable).
REASON_INACTIVE = "inactive"
REASON_INSUFFICIENT_UNITS = "insufficient_units"
REASON_NOT_FOUND = "not_found"
REASON_SIGNATURE_FAILED = "signature_failed"
REASON_INVALID_AMOUNT = "invalid_amount"

# date.weekday(): Monday == 0 ... Sunday == 6.
_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_DEFAULT_RESET_WEEKDAY = "monday"


class SignatureAppendError(Exception):
    """Raised when the Digital_Signature (sync_log) append fails during a draw.

    Used to trigger the roll-back of the decrement (Req 8.9). The property test
    for Property 17 injects a failing audit callable that raises this so the
    signature-failure path is exercised deterministically.
    """


# --------------------------------------------------------------------------- #
# Pure derivation helpers (no DB, no AI) -- Req 8.1, 9.4
# --------------------------------------------------------------------------- #


def _as_int(value: Any) -> int | None:
    """Best-effort integer coercion; return ``None`` when not coercible."""
    if isinstance(value, bool):  # guard: bool is a subclass of int
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def allowance_units(rules: Mapping[str, Any]) -> int | None:
    """Return the per-period allowance in integer-minute units (Req 8.1).

    ``hours_per_week``/``hours`` are converted to minutes; ``units``/``sessions``
    are used verbatim; a product with none of these is unlimited (``None``).
    """
    rules = rules or {}
    hours = _as_int(rules.get("hours_per_week"))
    if hours is None:
        hours = _as_int(rules.get("hours"))
    if hours is not None:
        return hours * MINUTES_PER_HOUR

    for key in ("units", "sessions"):
        count = _as_int(rules.get(key))
        if count is not None:
            return count
    return None


def is_recurring(rules: Mapping[str, Any]) -> bool:
    """True when the product rules describe a recurring allowance (Req 9.1).

    A rule is recurring when it names a ``reset`` weekday or carries a weekly
    allowance (``hours_per_week``).
    """
    rules = rules or {}
    return rules.get("reset") is not None or "hours_per_week" in rules


def _reset_weekday(rules: Mapping[str, Any]) -> int:
    """Return the weekday index (Mon=0..Sun=6) the period is anchored on."""
    reset = (rules or {}).get("reset")
    if isinstance(reset, str) and reset.strip().lower() in _WEEKDAYS:
        return _WEEKDAYS[reset.strip().lower()]
    return _WEEKDAYS[_DEFAULT_RESET_WEEKDAY]


def _local_date(now: datetime, location_timezone: str) -> date:
    """Return the calendar date of ``now`` in the configured location timezone."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(location_timezone)).date()


def period_start(
    rules: Mapping[str, Any],
    now: datetime,
    *,
    location_timezone: str = DEFAULT_LOCATION_TIMEZONE,
) -> date:
    """Return the deterministic weekly period start date for ``now`` (Req 9.4).

    The most recent occurrence (on or before today, in the location timezone) of
    the weekday named by ``rules.reset``.
    """
    today = _local_date(now, location_timezone)
    target = _reset_weekday(rules)
    delta = (today.weekday() - target) % 7
    return today - timedelta(days=delta)


def _add_months(anchor: date, months: int) -> date:
    """Return ``anchor`` shifted forward by ``months`` (clamping the day)."""
    zero_based = anchor.month - 1 + months
    year = anchor.year + zero_based // 12
    month = zero_based % 12 + 1
    # Clamp the day to the last valid day of the target month.
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_day = (next_month_first - timedelta(days=1)).day
    return date(year, month, min(anchor.day, last_day))


@dataclass(frozen=True)
class DerivedWindow:
    """The units + validity window derived from a product's rules (Req 8.1)."""

    remaining_units: int | None
    valid_from: date
    valid_to: date | None
    recurring: bool
    per_period_allowance: int | None


def derive_window(
    rules: Mapping[str, Any],
    now: datetime,
    *,
    location_timezone: str = DEFAULT_LOCATION_TIMEZONE,
) -> DerivedWindow:
    """Derive ``remaining_units`` and the validity window from ``rules`` (Req 8.1).

    Deterministic and AI-free. For a recurring product, ``valid_from`` is the
    current period start; ``valid_to`` is the term end when ``min_term_months``
    is given, the end of the current week when a ``fixed_window`` weekly product
    carries no term, or open-ended otherwise. For a non-recurring product,
    ``valid_from`` is today and ``valid_to`` follows ``valid_days`` when present.
    """
    rules = rules or {}
    allowance = allowance_units(rules)
    recurring = is_recurring(rules)
    today = _local_date(now, location_timezone)

    if recurring:
        start = period_start(rules, now, location_timezone=location_timezone)
    else:
        start = today

    valid_to: date | None = None
    term_months = _as_int(rules.get("min_term_months"))
    valid_days = _as_int(rules.get("valid_days"))
    if term_months is not None:
        valid_to = _add_months(start, term_months)
    elif valid_days is not None:
        valid_to = start + timedelta(days=valid_days)
    elif recurring and bool(rules.get("fixed_window")):
        valid_to = start + timedelta(days=7)

    return DerivedWindow(
        remaining_units=allowance,
        valid_from=start,
        valid_to=valid_to,
        recurring=recurring,
        per_period_allowance=allowance,
    )


@dataclass(frozen=True)
class ResetOutcome:
    """The outcome of evaluating a recurring reset (Req 9)."""

    changed: bool
    remaining_units: int | None
    valid_from: date


def reset_if_new_period(
    entitlement: Mapping[str, Any],
    product_rules: Mapping[str, Any],
    now: datetime,
    *,
    location_timezone: str = DEFAULT_LOCATION_TIMEZONE,
) -> ResetOutcome:
    """Compute the recurring reset for ``now``, without touching the DB (Req 9).

    For a recurring product, if the current period start is strictly later than
    the entitlement's stored ``valid_from`` (the period the current units belong
    to), the allowance is restored to the product's per-period allowance
    (unused prior-period units discarded -- **no rollover**, Req 9.2) and the new
    ``valid_from`` is the period start. Otherwise no change is reported. A
    non-recurring product never resets.

    Args:
        entitlement: A mapping carrying at least ``remaining_units`` and
            ``valid_from`` (a ``date`` or ``None``).
        product_rules: The product's ``rules`` mapping.
        now: The evaluation time.
        location_timezone: Timezone the weekly boundary is computed in.

    Returns:
        A :class:`ResetOutcome`. ``changed`` is ``True`` only when a reset is due.
    """
    current_from = entitlement.get("valid_from")
    current_units = entitlement.get("remaining_units")

    if not is_recurring(product_rules):
        return ResetOutcome(False, current_units, current_from)

    start = period_start(product_rules, now, location_timezone=location_timezone)

    # No stored period yet -> adopt the current period without resetting units.
    if current_from is None:
        return ResetOutcome(False, current_units, start)

    if start > current_from:
        return ResetOutcome(True, allowance_units(product_rules), start)

    return ResetOutcome(False, current_units, current_from)


# --------------------------------------------------------------------------- #
# DB-backed operations -- Req 8
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EntitlementRow:
    """A created/updated entitlement, returned to callers."""

    id: Any
    player_id: Any
    product_id: Any
    status: str
    remaining_units: int | None
    valid_from: date | None
    valid_to: date | None
    location_id: Any


@dataclass(frozen=True)
class DrawResult:
    """The outcome of a :func:`draw` (Req 8.2-8.5, 8.8, 8.9)."""

    status: str  # DRAW_APPLIED | DRAW_REJECTED
    entitlement_id: Any
    remaining_units: int | None
    reason: str | None = None
    signature_id: Any | None = None

    @property
    def applied(self) -> bool:
        return self.status == DRAW_APPLIED

    @property
    def skipped(self) -> bool:
        return self.status == DRAW_SKIPPED


def _fetch_product_rules(cursor: Any, product_id: Any) -> dict[str, Any]:
    """Return a product's ``rules`` JSONB as a dict (empty when absent)."""
    cursor.execute("SELECT rules FROM products WHERE id = %s", (product_id,))
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return {}
    rules = row[0]
    return dict(rules) if isinstance(rules, Mapping) else {}


def create_entitlement(
    conn: Any,
    *,
    player_id: Any,
    product_id: Any,
    location_id: Any,
    logged_by: Any | None = None,
    now: datetime | None = None,
    location_timezone: str = DEFAULT_LOCATION_TIMEZONE,
    device_id: Any | None = None,
    client_id: Any | None = None,
) -> EntitlementRow:
    """Create an entitlement with units/window derived from product rules (Req 8.1).

    The write and its ``sync_log`` insert share one transaction so the audit
    entry is atomic with the row (Req 14.2). The entitlement payload is passed
    through ``popia.filter_payload`` first as a defensive POPIA measure (Req
    14.1); entitlement fields carry no prohibited data, so nothing is dropped in
    practice.

    Args:
        conn: An open psycopg connection (caller owns lifecycle; committed here).
        player_id: Subject player.
        product_id: The product the entitlement is sold under.
        location_id: The row's location scope (typically the player's location).
        logged_by: Acting user recorded on the audit entry.
        now: Creation time (defaults to ``datetime.now(timezone.utc)``).
        location_timezone: Timezone for deterministic period derivation.
        device_id / client_id: Optional sync metadata.

    Returns:
        The created :class:`EntitlementRow`.
    """
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cursor:
        rules = _fetch_product_rules(cursor, product_id)
        window = derive_window(rules, now, location_timezone=location_timezone)

        # Defensive POPIA filter over the entitlement payload (Req 14.1).
        payload = {
            "player_id": player_id,
            "product_id": product_id,
            "remaining_units": window.remaining_units,
            "valid_from": window.valid_from,
            "valid_to": window.valid_to,
            "location_id": location_id,
        }
        clean, _dropped = filter_payload(payload)

        cursor.execute(
            """
            INSERT INTO entitlements
                (player_id, product_id, status, remaining_units, valid_from,
                 valid_to, location_id, client_id, device_id, client_timestamp)
            VALUES (%s, %s, 'active', %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, player_id, product_id, status, remaining_units,
                      valid_from, valid_to, location_id
            """,
            (
                clean["player_id"],
                clean["product_id"],
                clean["remaining_units"],
                clean["valid_from"],
                clean["valid_to"],
                clean["location_id"],
                client_id,
                device_id,
                now,
            ),
        )
        row = cursor.fetchone()
        entitlement_id = row[0]

        append_sync_log(
            cursor,
            entity="entitlements",
            record_id=entitlement_id,
            action=ACTION_INSERT,
            location_id=location_id,
            user_id=logged_by,
            device_id=device_id,
            client_id=client_id,
            client_timestamp=now,
        )

    conn.commit()
    return EntitlementRow(
        id=row[0],
        player_id=row[1],
        product_id=row[2],
        status=row[3],
        remaining_units=row[4],
        valid_from=row[5],
        valid_to=row[6],
        location_id=row[7],
    )


def draw(
    conn: Any,
    entitlement_id: Any,
    amount: int,
    *,
    logged_by: Any | None,
    now: datetime | None = None,
    location_timezone: str = DEFAULT_LOCATION_TIMEZONE,
    device_id: Any | None = None,
    client_id: Any | None = None,
    audit_append: Callable[..., Any] | None = None,
) -> DrawResult:
    """Draw ``amount`` units from an entitlement, recording a signature (Req 8).

    Algorithm:

    1. Lock the entitlement row ``FOR UPDATE`` (serialises concurrent draws).
    2. For an offline sync action, check the stable ``client_id`` while holding
       that lock. A previously applied action is returned as ``skipped`` before
       any reset or balance change.
    3. If the product is recurring, apply :func:`reset_if_new_period` first and
       persist the reset (Req 9.3). The reset persists even when the draw is
       subsequently rejected -- the new period's allowance is real.
    4. Reject (units unchanged) when the status is not ``active`` (Req 8.5) or
       ``remaining_units < amount`` (Req 8.4), returning a ``rejected`` result.
    5. Otherwise decrement ``remaining_units`` by ``amount`` and append the
       **Digital_Signature** -- a ``sync_log`` ``update`` entry pairing the
       acting user with a server timestamp and, when supplied, the stable client
       identity (Req 8.3, 8.8) -- inside a savepoint.
    6. If the signature append raises, roll the decrement back to the savepoint
       so the units are left unchanged, then report the failure (Req 8.9).

    Args:
        conn: An open psycopg connection (non-autocommit; committed here).
        entitlement_id: The entitlement to draw from.
        amount: Units to draw (must be a positive integer).
        logged_by: Acting user recorded as the signature's actor.
        now: Draw time (defaults to ``datetime.now(timezone.utc)``).
        location_timezone: Timezone for the recurring reset boundary.
        device_id: Optional device id for the audit entry.
        client_id: Optional stable offline-action identity. When already present
            on an applied audit row, the draw is an idempotent no-op.
        audit_append: Injectable audit function (defaults to
            :func:`append_sync_log`); the Property 17 test injects a failing
            callable to exercise the roll-back path.

    Returns:
        A :class:`DrawResult`.
    """
    now = now or datetime.now(timezone.utc)
    append_fn = audit_append if audit_append is not None else append_sync_log

    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        return DrawResult(
            status=DRAW_REJECTED,
            entitlement_id=entitlement_id,
            remaining_units=None,
            reason=REASON_INVALID_AMOUNT,
        )

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, remaining_units, valid_from, product_id, location_id
            FROM entitlements
            WHERE id = %s
            FOR UPDATE
            """,
            (entitlement_id,),
        )
        row = cursor.fetchone()
        if row is None:
            conn.rollback()
            return DrawResult(
                status=DRAW_REJECTED,
                entitlement_id=entitlement_id,
                remaining_units=None,
                reason=REASON_NOT_FOUND,
            )

        status, remaining_units, valid_from, product_id, location_id = row

        # A stable sync client_id is the idempotency identity (Req 4.2). Check
        # only after locking the entitlement so concurrent replays of the same
        # draw cannot both pass the lookup and decrement. This happens before a
        # recurring reset because a replay must be a complete no-op.
        if client_id is not None:
            cursor.execute(
                "SELECT record_id FROM sync_log WHERE client_id = %s LIMIT 1",
                (client_id,),
            )
            receipt_exists = cursor.fetchone() is not None
            legacy_receipt = False
            if not receipt_exists:
                # Transition safety: an old API revision keyed draw replay on
                # entitlement + timestamp and could not persist client_id. Only
                # rows marked by migration 010 participate in this fallback;
                # direct draws written afterwards keep the default FALSE.
                cursor.execute(
                    "SELECT 1 FROM sync_log "
                    "WHERE legacy_client_id_missing = TRUE "
                    "AND entity = 'entitlements' AND record_id = %s "
                    "AND action = 'update' AND client_timestamp = %s LIMIT 1",
                    (entitlement_id, now),
                )
                legacy_receipt = cursor.fetchone() is not None

            if receipt_exists or legacy_receipt:
                append_fn(
                    cursor,
                    entity="entitlements",
                    record_id=entitlement_id,
                    action=ACTION_SKIP,
                    location_id=location_id,
                    user_id=logged_by,
                    device_id=device_id,
                    # Claim the real identity when upgrading a legacy receipt;
                    # ordinary replays leave it NULL to preserve uniqueness.
                    client_id=client_id if legacy_receipt else None,
                    client_timestamp=now,
                )
                conn.commit()
                return DrawResult(
                    status=DRAW_SKIPPED,
                    entitlement_id=entitlement_id,
                    remaining_units=remaining_units,
                )

        rules = _fetch_product_rules(cursor, product_id)

        # (2) Apply recurring reset before evaluating the draw (Req 9.3).
        reset = reset_if_new_period(
            {"remaining_units": remaining_units, "valid_from": valid_from},
            rules,
            now,
            location_timezone=location_timezone,
        )
        if reset.changed:
            remaining_units = reset.remaining_units
            valid_from = reset.valid_from
            cursor.execute(
                "UPDATE entitlements SET remaining_units = %s, valid_from = %s, "
                "updated_at = now() WHERE id = %s",
                (remaining_units, valid_from, entitlement_id),
            )

        # (3) Reject inactive / under-funded draws, leaving units unchanged.
        if status != STATUS_ACTIVE:
            conn.commit()  # persist any reset; the draw itself changes nothing
            return DrawResult(
                status=DRAW_REJECTED,
                entitlement_id=entitlement_id,
                remaining_units=remaining_units,
                reason=REASON_INACTIVE,
            )

        # Unlimited entitlement (no unit cap): there is no decrement or digital
        # signature. A sync action still records a non-signature receipt so its
        # next delivery is skipped by stable client identity.
        if remaining_units is None:
            if client_id is not None:
                append_fn(
                    cursor,
                    entity="entitlements",
                    record_id=entitlement_id,
                    action=ACTION_SKIP,
                    location_id=location_id,
                    user_id=logged_by,
                    device_id=device_id,
                    client_id=client_id,
                    client_timestamp=now,
                )
            conn.commit()
            return DrawResult(
                status=DRAW_APPLIED,
                entitlement_id=entitlement_id,
                remaining_units=None,
            )

        if remaining_units < amount:
            conn.commit()
            return DrawResult(
                status=DRAW_REJECTED,
                entitlement_id=entitlement_id,
                remaining_units=remaining_units,
                reason=REASON_INSUFFICIENT_UNITS,
            )

        new_units = remaining_units - amount

        # (4/5) Decrement + signature share a savepoint so a signature failure
        # rolls back ONLY the decrement (the reset above is preserved) (Req 8.9).
        try:
            with conn.transaction():
                cursor.execute(
                    "UPDATE entitlements SET remaining_units = %s, updated_at = now() "
                    "WHERE id = %s",
                    (new_units, entitlement_id),
                )
                signature_id = append_fn(
                    cursor,
                    entity="entitlements",
                    record_id=entitlement_id,
                    action=ACTION_UPDATE,
                    location_id=location_id,
                    user_id=logged_by,
                    device_id=device_id,
                    client_id=client_id,
                    client_timestamp=now,
                )
        except SignatureAppendError:
            # The signature could not be recorded: the savepoint rolled the
            # decrement back, so units are unchanged. Persist the reset and
            # report the failure (Req 8.9).
            conn.commit()
            return DrawResult(
                status=DRAW_REJECTED,
                entitlement_id=entitlement_id,
                remaining_units=remaining_units,
                reason=REASON_SIGNATURE_FAILED,
            )

        conn.commit()
        return DrawResult(
            status=DRAW_APPLIED,
            entitlement_id=entitlement_id,
            remaining_units=new_units,
            signature_id=signature_id,
        )


@dataclass(frozen=True)
class Balance:
    """A single active entitlement's balance within scope (Req 8.6)."""

    entitlement_id: Any
    product_id: Any
    remaining_units: int | None
    valid_from: date | None
    valid_to: date | None
    status: str


def balance(
    conn: Any,
    player_id: Any,
    scope: Any,
) -> list[Balance]:
    """Return active entitlement balances for ``player_id`` within ``scope`` (Req 8.6).

    Entitlements carry ``location_id`` but not ``school_id``; a facilitator's
    school constraint is applied by joining to ``players``. A founder sees all;
    a manager is constrained to its location; a facilitator to its location and
    the player's school.
    """
    conditions = ["e.player_id = %s", "e.status = %s"]
    params: list[Any] = [player_id, STATUS_ACTIVE]

    if not getattr(scope, "unrestricted", False):
        location_id = getattr(scope, "location_id", None)
        conditions.append("e.location_id = %s")
        params.append(location_id)
        if getattr(scope, "role", None) == "facilitator":
            conditions.append("p.school_id = %s")
            params.append(getattr(scope, "school_id", None))

    where = " AND ".join(conditions)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT e.id, e.product_id, e.remaining_units, e.valid_from,
                   e.valid_to, e.status
            FROM entitlements e
            JOIN players p ON p.id = e.player_id
            WHERE {where}
            ORDER BY e.created_at
            """,
            params,
        )
        rows = cursor.fetchall()

    return [
        Balance(
            entitlement_id=r[0],
            product_id=r[1],
            remaining_units=r[2],
            valid_from=r[3],
            valid_to=r[4],
            status=r[5],
        )
        for r in rows
    ]
