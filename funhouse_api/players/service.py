"""Players service: roster, registration, and history (Req 6, 8.7, 15).

This service is orchestration over reused Phase 0 Load logic, exactly as the
design prescribes ("reuse, not reimplementation"):

* **Roster** (:func:`list_players`) applies the caller's ``Scope.read_filter``
  so no out-of-scope player is ever returned (Req 6.1, 15.1, 15.2).
* **Registration** (:func:`register_player`) stamps the row to the caller's
  scope (Req 15.3), resolves the player through the Phase 0 dedup layer
  (:func:`funhouse_pipeline.load.dedup.resolve_players` /
  :func:`compute_dedup_key`) so a re-registration resolves to the existing row
  rather than creating a duplicate (Req 6.5), appends one row per supplied
  consent type through the append-only ledger
  (:func:`funhouse_pipeline.load.consent.append_consent`, Req 6.3, 6.4), runs
  ``popia.filter_payload`` defensively (Req 14.1), and appends a ``sync_log``
  entry for the created player in the same transaction as the write (Req 14.2).
* **History** (:func:`player_history`) returns the player's sessions, payments,
  and entitlement draws restricted to the caller's scope, so it is complete
  within scope and leaks nothing outside it (Req 6.7, 6.8, 8.7).

Validation (missing ``first_name`` -> 422, zero consents -> 422) is enforced by
the Pydantic request model in the router, so this layer receives already-valid
input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.audit import ACTION_INSERT, append_sync_log
from funhouse_pipeline.load.consent import append_consent
from funhouse_pipeline.load.dedup import resolve_players
from funhouse_pipeline.load.popia import filter_payload


class RegistrationError(Exception):
    """Raised when a registration cannot be resolved to a single player row.

    Mapped to ``422`` by the router (e.g. the dedup layer flagged the candidate
    as ambiguous or nameless). Missing ``first_name``/zero consents are caught
    earlier by Pydantic.
    """


@dataclass(frozen=True)
class ConsentInput:
    """One consent to append at registration (Req 6.3)."""

    consent_type: str
    granted: bool = True
    method: str | None = None
    granted_at: Any | None = None


def _scope_conditions(
    scope: Any,
    *,
    location_col: str,
    school_col: str | None,
) -> tuple[list[str], list[Any]]:
    """Build scope ``WHERE`` conditions for a table (Req 3.2, 3.3, 15.1, 15.2).

    A founder is unrestricted (no conditions). A manager is constrained by
    ``location_col``; a facilitator additionally by ``school_col`` (which the
    caller supplies from whichever joined table actually carries ``school_id``).
    """
    if getattr(scope, "unrestricted", False):
        return [], []
    conditions = [f"{location_col} = %s"]
    params: list[Any] = [scope.location_id]
    if getattr(scope, "role", None) == "facilitator" and school_col is not None:
        conditions.append(f"{school_col} = %s")
        params.append(scope.school_id)
    return conditions, params


@dataclass(frozen=True)
class PlayerOut:
    """A player row within scope (roster / registration response)."""

    id: Any
    first_name: str
    last_name: str | None
    birth_date: Any | None
    grade: str | None
    school_id: Any | None
    location_id: Any
    consent_status: str
    active: bool


def _row_to_player(row: Sequence[Any]) -> PlayerOut:
    return PlayerOut(
        id=row[0],
        first_name=row[1],
        last_name=row[2],
        birth_date=row[3],
        grade=row[4],
        school_id=row[5],
        location_id=row[6],
        consent_status=row[7],
        active=row[8],
    )


_PLAYER_COLUMNS = (
    "id, first_name, last_name, birth_date, grade, school_id, "
    "location_id, consent_status, active"
)


def list_players(conn: Any, scope: Any) -> list[PlayerOut]:
    """Return the roster within the caller's scope (Req 6.1, 15.1, 15.2)."""
    conditions, params = _scope_conditions(
        scope, location_col="location_id", school_col="school_id"
    )
    where = " AND ".join(conditions) if conditions else "TRUE"
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT {_PLAYER_COLUMNS} FROM players WHERE {where} ORDER BY created_at",
            params,
        )
        rows = cursor.fetchall()
    return [_row_to_player(r) for r in rows]


def register_player(
    conn: Any,
    scope: Any,
    *,
    logged_by: Any,
    first_name: str,
    consents: Sequence[ConsentInput],
    last_name: str | None = None,
    birth_date: Any | None = None,
    grade: str | None = None,
    school_id: Any | None = None,
    location_id: Any | None = None,
    now: datetime | None = None,
    device_id: Any | None = None,
) -> PlayerOut:
    """Register (or resolve) a player and append its consents (Req 6.2-6.5, 15.3).

    The player is stamped to the caller's scope, resolved through the Phase 0
    dedup layer (so a duplicate registration returns the existing row), and each
    consent type is appended to the append-only ledger. The created player's
    ``sync_log`` insert shares the dedup transaction so the audit entry is atomic
    with the write; the consent appends each carry their own audited transaction.

    Args:
        conn: An open psycopg connection (non-autocommit; committed here).
        scope: The caller's :class:`~funhouse_api.rbac.Scope`.
        logged_by: Acting user id recorded on audit entries.
        first_name: Required (validated upstream).
        consents: One or more consents to append (validated non-empty upstream).
        last_name/birth_date/grade/school_id: Optional player attributes.
        location_id: Optional caller-supplied location (used for a founder, who
            has no scope location); overridden by scope for manager/facilitator.
        now: Registration time (defaults to now, UTC).
        device_id: Optional sync metadata.

    Returns:
        The resolved :class:`PlayerOut`.

    Raises:
        RegistrationError: When the caller supplied no usable location, or the
            dedup layer could not resolve the candidate to a single row.
    """
    now = now or datetime.now(timezone.utc)

    # Stamp location/school to the caller's scope (Req 15.3). A founder keeps the
    # supplied values; a manager/facilitator has them overridden to its scope.
    stamped: dict[str, Any] = {"location_id": location_id, "school_id": school_id}
    scope.stamp(stamped)
    effective_location = stamped.get("location_id")
    effective_school = stamped.get("school_id")
    if effective_location is None:
        raise RegistrationError("a location is required to register a player")

    # Defensive POPIA filter over the player payload (Req 14.1).
    raw_payload = {
        "first_name": first_name,
        "last_name": last_name,
        "birth_date": birth_date,
        "grade": grade,
    }
    clean, _dropped = filter_payload(raw_payload)

    record = ExtractedRecord(
        record_id="register",
        target_table="players",
        payload=dict(clean),
        confidence_score=1.0,
        source_file="api:register",
        provider="api",
        extracted_at=now,
    )

    # Resolve (create or merge) the player through the Phase 0 dedup layer and
    # audit a created row inside the same transaction (Req 6.5, 14.2).
    with conn.transaction():
        resolution = resolve_players(
            [record], conn, location_id=effective_location
        )
        player_id = resolution.resolved.get("register")
        if player_id is None:
            # Ambiguous or nameless per the dedup layer -> validation error.
            raise RegistrationError("could not resolve the player unambiguously")

        with conn.cursor() as cursor:
            # Attach the school FK for a newly created row (dedup does not set it).
            if effective_school is not None:
                cursor.execute(
                    "UPDATE players SET school_id = COALESCE(school_id, %s) "
                    "WHERE id = %s",
                    (effective_school, player_id),
                )
            if player_id in resolution.created:
                append_sync_log(
                    cursor,
                    entity="players",
                    record_id=player_id,
                    action=ACTION_INSERT,
                    location_id=effective_location,
                    user_id=logged_by,
                    device_id=device_id,
                    client_timestamp=now,
                )

    # Append one consent row per supplied consent type (append-only, Req 6.3,
    # 6.4). Each append audits itself in its own transaction.
    for consent in consents:
        append_consent(
            conn,
            player_id=player_id,
            consent_type=consent.consent_type,
            granted=consent.granted,
            location_id=effective_location,
            granted_at=consent.granted_at,
            method=consent.method,
            captured_by_user_id=logged_by,
            device_id=device_id,
            client_timestamp=now,
        )

    conn.commit()

    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT {_PLAYER_COLUMNS} FROM players WHERE id = %s", (player_id,)
        )
        row = cursor.fetchone()
    return _row_to_player(row)


@dataclass(frozen=True)
class PlayerHistory:
    """A player's in-scope history (Req 6.7, 8.7)."""

    player_id: Any
    sessions: list[dict[str, Any]]
    payments: list[dict[str, Any]]
    entitlement_draws: list[dict[str, Any]]


def player_history(conn: Any, scope: Any, player_id: Any) -> PlayerHistory:
    """Return the player's sessions, payments, and draws within scope (Req 6.7).

    Each sub-resource is filtered by the caller's scope independently, so the
    result is complete within scope and contains no out-of-scope record; when
    none of the player's history falls in scope every list is empty (Req 6.8,
    Property 15). Each entitlement draw carries its acting user and server
    timestamp (Req 8.7).
    """
    # Sessions (carry location_id + school_id directly).
    s_conditions, s_params = _scope_conditions(
        scope, location_col="location_id", school_col="school_id"
    )
    s_where = " AND ".join(["player_id = %s", *s_conditions])
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, session_type, started_at, ended_at, duration_minutes,
                   school_id, logged_by, location_id
            FROM sessions
            WHERE {s_where}
            ORDER BY created_at
            """,
            [player_id, *s_params],
        )
        sessions = [
            {
                "id": r[0],
                "session_type": r[1],
                "started_at": r[2],
                "ended_at": r[3],
                "duration_minutes": r[4],
                "school_id": r[5],
                "logged_by": r[6],
                "location_id": r[7],
            }
            for r in cursor.fetchall()
        ]

    # Payments (carry location_id but no school_id -> facilitator school via the
    # players join).
    p_conditions, p_params = _scope_conditions(
        scope, location_col="pay.location_id", school_col="pl.school_id"
    )
    p_where = " AND ".join(["pay.player_id = %s", *p_conditions])
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT pay.id, pay.product_id, pay.amount_cents, pay.method,
                   pay.paid_at, pay.logged_by, pay.location_id
            FROM payments pay
            JOIN players pl ON pl.id = pay.player_id
            WHERE {p_where}
            ORDER BY pay.created_at
            """,
            [player_id, *p_params],
        )
        payments = [
            {
                "id": r[0],
                "product_id": r[1],
                "amount_cents": r[2],
                "method": r[3],
                "paid_at": r[4],
                "logged_by": r[5],
                "location_id": r[6],
            }
            for r in cursor.fetchall()
        ]

    # Entitlement draws: sync_log 'update' entries for the player's entitlements,
    # each carrying the acting user + server timestamp (the digital signature).
    d_conditions, d_params = _scope_conditions(
        scope, location_col="e.location_id", school_col="pl.school_id"
    )
    d_where = " AND ".join(
        [
            "e.player_id = %s",
            "sl.entity = 'entitlements'",
            "sl.action = 'update'",
            *d_conditions,
        ]
    )
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT sl.record_id, sl.user_id, sl.server_timestamp,
                   sl.client_timestamp, e.product_id
            FROM sync_log sl
            JOIN entitlements e ON e.id = sl.record_id
            JOIN players pl ON pl.id = e.player_id
            WHERE {d_where}
            ORDER BY sl.server_timestamp
            """,
            [player_id, *d_params],
        )
        draws = [
            {
                "entitlement_id": r[0],
                "logged_by": r[1],
                "server_timestamp": r[2],
                "client_timestamp": r[3],
                "product_id": r[4],
            }
            for r in cursor.fetchall()
        ]

    return PlayerHistory(
        player_id=player_id,
        sessions=sessions,
        payments=payments,
        entitlement_draws=draws,
    )
