"""Player HTTP endpoints (Req 6, 8.7, 15).

Thin FastAPI router over :mod:`funhouse_api.players.service`. Every endpoint is
authenticated (``require_auth``) and scoped (``require_scope``); the acting user
(``logged_by``) is taken from the verified :class:`Principal`.

* ``GET /players`` -- roster within scope (Req 6.1).
* ``POST /players`` -- register a player with one or more consents. A missing
  ``first_name`` or an empty ``consents`` list is a Pydantic validation error ->
  ``422`` (Req 6.6, 6.9). The row is stamped to the caller's scope and
  deduplicated (Req 6.2, 6.5, 15.3).
* ``GET /players/{id}/history`` -- the player's in-scope sessions, payments, and
  entitlement draws (Req 6.7, 6.8, 8.7).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field

from funhouse_api.auth.dependencies import Principal, require_auth
from funhouse_api.db import get_connection
from funhouse_api.players import service
from funhouse_api.rbac import Scope, require_scope

router = APIRouter(tags=["players"])


class ConsentInputModel(BaseModel):
    """A single consent supplied at registration (Req 6.3)."""

    consent_type: str
    granted: bool = True
    method: str | None = None
    granted_at: datetime | None = None


class PlayerCreate(BaseModel):
    """Registration payload.

    ``first_name`` is required -> 422 if missing (Req 6.6). ``consents`` must be
    non-empty -> 422 if empty (Req 6.9).
    """

    first_name: str = Field(min_length=1)
    last_name: str | None = None
    birth_date: date | None = None
    grade: str | None = None
    school_id: UUID | None = None
    location_id: UUID | None = None
    consents: list[ConsentInputModel] = Field(min_length=1)


class PlayerOutModel(BaseModel):
    """A player row within scope."""

    id: UUID
    first_name: str
    last_name: str | None
    birth_date: date | None
    grade: str | None
    school_id: UUID | None
    location_id: UUID
    consent_status: str
    active: bool


class PlayerHistoryModel(BaseModel):
    """A player's in-scope history (Req 6.7, 8.7)."""

    player_id: UUID
    sessions: list[dict[str, Any]]
    payments: list[dict[str, Any]]
    entitlement_draws: list[dict[str, Any]]


def _to_out_model(player: service.PlayerOut) -> PlayerOutModel:
    return PlayerOutModel(
        id=player.id,
        first_name=player.first_name,
        last_name=player.last_name,
        birth_date=player.birth_date,
        grade=player.grade,
        school_id=player.school_id,
        location_id=player.location_id,
        consent_status=player.consent_status,
        active=player.active,
    )


@router.get("/players", response_model=list[PlayerOutModel])
def list_players(
    scope: Scope = Depends(require_scope),
    conn: Any = Depends(get_connection),
) -> list[PlayerOutModel]:
    """Return the roster within scope (Req 6.1)."""
    return [_to_out_model(p) for p in service.list_players(conn, scope)]


@router.post("/players", response_model=PlayerOutModel, status_code=201)
def register_player(
    body: PlayerCreate,
    principal: Principal = Depends(require_auth),
    scope: Scope = Depends(require_scope),
    conn: Any = Depends(get_connection),
) -> PlayerOutModel:
    """Register (or resolve) a player and append its consents (Req 6.2-6.5)."""
    consents = [
        service.ConsentInput(
            consent_type=c.consent_type,
            granted=c.granted,
            method=c.method,
            granted_at=c.granted_at,
        )
        for c in body.consents
    ]
    try:
        player = service.register_player(
            conn,
            scope,
            logged_by=principal.user_id,
            first_name=body.first_name,
            consents=consents,
            last_name=body.last_name,
            birth_date=body.birth_date,
            grade=body.grade,
            school_id=None if body.school_id is None else str(body.school_id),
            location_id=None if body.location_id is None else str(body.location_id),
        )
    except service.RegistrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_out_model(player)


@router.get("/players/{player_id}/history", response_model=PlayerHistoryModel)
def player_history(
    player_id: UUID,
    scope: Scope = Depends(require_scope),
    conn: Any = Depends(get_connection),
) -> PlayerHistoryModel:
    """Return the player's in-scope history (Req 6.7, 6.8, 8.7)."""
    history = service.player_history(conn, scope, str(player_id))
    return PlayerHistoryModel(
        player_id=player_id,
        sessions=history.sessions,
        payments=history.payments,
        entitlement_draws=history.entitlement_draws,
    )
