"""Entitlement HTTP endpoints (Req 8.1, 8.2, 8.6).

Thin FastAPI router over the :mod:`funhouse_api.entitlements.engine`. Every
endpoint is authenticated (``require_auth``) and scoped (``require_scope``); the
acting user (``logged_by``) is taken from the verified :class:`Principal`.

* ``POST /entitlements`` -- create an entitlement from a sell action. Restricted
  to ``manager``/``founder`` (Req 8.1); the row inherits the *player's* location
  scope and the caller must be allowed to write there (Req 3.5, 15.3).
* ``POST /entitlements/{id}/draw`` -- decrement units and record the digital
  signature (Req 8.2-8.5, 8.8, 8.9). Insufficient/inactive draws -> ``409``.
* ``GET /players/{id}/entitlements`` -- scoped balance query (Req 8.6).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from funhouse_api.auth.dependencies import Principal, require_auth
from funhouse_api.config import ApiConfig
from funhouse_api.db import get_connection
from funhouse_api.dependencies import get_api_config
from funhouse_api.entitlements import engine
from funhouse_api.rbac import AuthzError, Scope, require_scope

router = APIRouter(tags=["entitlements"])

# Roles permitted to create (sell) an entitlement (Req 8.1).
_CREATE_ROLES = {"manager", "founder"}


class EntitlementCreate(BaseModel):
    """Create-entitlement payload (Req 8.1)."""

    player_id: UUID
    product_id: UUID


class EntitlementOut(BaseModel):
    """A created entitlement."""

    id: UUID
    player_id: UUID
    product_id: UUID
    status: str
    remaining_units: int | None
    valid_from: Any | None
    valid_to: Any | None


class DrawInput(BaseModel):
    """Draw payload (Req 8.2). ``amount`` is in the entitlement's minute-units."""

    amount: int


class DrawOut(BaseModel):
    """Draw result on success."""

    entitlement_id: UUID
    status: str
    remaining_units: int | None


class BalanceOut(BaseModel):
    """A single active entitlement balance (Req 8.6)."""

    entitlement_id: UUID
    product_id: UUID
    remaining_units: int | None
    valid_from: Any | None
    valid_to: Any | None
    status: str


def _load_player_scope(conn: Any, player_id: UUID) -> tuple[Any, Any]:
    """Return ``(location_id, school_id)`` for a player, or 404 if absent."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT location_id, school_id FROM players WHERE id = %s",
            (str(player_id),),
        )
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return row[0], row[1]


def _load_entitlement_scope(conn: Any, entitlement_id: UUID) -> tuple[Any, Any]:
    """Return ``(location_id, player_school_id)`` for an entitlement, or 404."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.location_id, p.school_id
            FROM entitlements e
            JOIN players p ON p.id = e.player_id
            WHERE e.id = %s
            """,
            (str(entitlement_id),),
        )
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entitlement not found")
    return row[0], row[1]


@router.post("/entitlements", response_model=EntitlementOut, status_code=201)
def create_entitlement(
    body: EntitlementCreate,
    principal: Principal = Depends(require_auth),
    scope: Scope = Depends(require_scope),
    config: ApiConfig = Depends(get_api_config),
    conn: Any = Depends(get_connection),
) -> EntitlementOut:
    """Create an entitlement from a sell action (Req 8.1, manager/founder only)."""
    if scope.role not in _CREATE_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden")

    location_id, school_id = _load_player_scope(conn, body.player_id)
    try:
        scope.assert_can_write(location_id, school_id)
    except AuthzError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc

    created = engine.create_entitlement(
        conn,
        player_id=str(body.player_id),
        product_id=str(body.product_id),
        location_id=location_id,
        logged_by=principal.user_id,
        now=datetime.now(timezone.utc),
        location_timezone=config.location_timezone,
    )
    return EntitlementOut(
        id=created.id,
        player_id=created.player_id,
        product_id=created.product_id,
        status=created.status,
        remaining_units=created.remaining_units,
        valid_from=created.valid_from,
        valid_to=created.valid_to,
    )


@router.post("/entitlements/{entitlement_id}/draw", response_model=DrawOut)
def draw_entitlement(
    entitlement_id: UUID,
    body: DrawInput,
    principal: Principal = Depends(require_auth),
    scope: Scope = Depends(require_scope),
    config: ApiConfig = Depends(get_api_config),
    conn: Any = Depends(get_connection),
) -> DrawOut:
    """Decrement units and record the digital signature (Req 8.2-8.5, 8.8, 8.9)."""
    if scope.role not in _CREATE_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden")
    location_id, school_id = _load_entitlement_scope(conn, entitlement_id)
    try:
        scope.assert_can_write(location_id, school_id)
    except AuthzError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc

    result = engine.draw(
        conn,
        str(entitlement_id),
        body.amount,
        logged_by=principal.user_id,
        now=datetime.now(timezone.utc),
        location_timezone=config.location_timezone,
    )
    if not result.applied:
        # Insufficient units / inactive / bad amount -> 409 Conflict (design
        # § Error Handling); units are left unchanged.
        raise HTTPException(status_code=409, detail=result.reason or "Draw rejected")

    return DrawOut(
        entitlement_id=entitlement_id,
        status=result.status,
        remaining_units=result.remaining_units,
    )


@router.get("/players/{player_id}/entitlements", response_model=list[BalanceOut])
def player_entitlements(
    player_id: UUID,
    scope: Scope = Depends(require_scope),
    conn: Any = Depends(get_connection),
) -> list[BalanceOut]:
    """Return the player's active entitlement balances within scope (Req 8.6)."""
    if scope.role not in _CREATE_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden")
    balances = engine.balance(conn, str(player_id), scope)
    return [
        BalanceOut(
            entitlement_id=b.entitlement_id,
            product_id=b.product_id,
            remaining_units=b.remaining_units,
            valid_from=b.valid_from,
            valid_to=b.valid_to,
            status=b.status,
        )
        for b in balances
    ]
