"""Session HTTP endpoint (Req 7, 15).

``POST /sessions`` logs a session scoped to the caller's location with
``logged_by`` set to the verified principal (Req 7.1, 15.3), optionally
associating a payment (Req 7.2) and/or drawing down an entitlement (Req 7.3). A
player outside the caller's scope -> ``403`` (Req 7.4, 7.6); a rejected draw ->
``409`` (Req 7.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field

from funhouse_api.auth.dependencies import Principal, require_auth
from funhouse_api.config import ApiConfig
from funhouse_api.db import get_connection
from funhouse_api.dependencies import get_api_config
from funhouse_api.rbac import Scope, require_scope
from funhouse_api.sessions import service

router = APIRouter(tags=["sessions"])

_SESSION_TYPES = {"lesson", "kit", "esports", "lounge"}


class PaymentInputModel(BaseModel):
    """Optional payment on a session (Req 7.2)."""

    amount_cents: int
    product_id: UUID | None = None
    method: str | None = None
    paid_at: datetime | None = None


class DrawInputModel(BaseModel):
    """Optional entitlement draw on a session (Req 7.3)."""

    entitlement_id: UUID
    amount: int = Field(gt=0)


class SessionCreate(BaseModel):
    """Session-log payload (Req 7.1)."""

    player_id: UUID
    session_type: str
    duration_minutes: int
    started_at: datetime | None = None
    ended_at: datetime | None = None
    school_id: UUID | None = None
    payment: PaymentInputModel | None = None
    draw: DrawInputModel | None = None


class SessionOutModel(BaseModel):
    """The logged session."""

    id: UUID
    player_id: UUID
    location_id: UUID
    logged_by: UUID | None
    payment_id: UUID | None = None
    draw_entitlement_id: UUID | None = None
    draw_remaining_units: int | None = None


@router.post("/sessions", response_model=SessionOutModel, status_code=201)
def create_session(
    body: SessionCreate,
    principal: Principal = Depends(require_auth),
    scope: Scope = Depends(require_scope),
    config: ApiConfig = Depends(get_api_config),
    conn: Any = Depends(get_connection),
) -> SessionOutModel:
    """Log a session with optional payment/draw within scope (Req 7)."""
    if body.session_type not in _SESSION_TYPES:
        raise HTTPException(status_code=422, detail="invalid session_type")

    payment = (
        service.PaymentInput(
            amount_cents=body.payment.amount_cents,
            product_id=None
            if body.payment.product_id is None
            else str(body.payment.product_id),
            method=body.payment.method,
            paid_at=body.payment.paid_at,
        )
        if body.payment is not None
        else None
    )
    draw = (
        service.DrawInput(
            entitlement_id=str(body.draw.entitlement_id), amount=body.draw.amount
        )
        if body.draw is not None
        else None
    )

    try:
        result = service.log_session(
            conn,
            scope,
            logged_by=principal.user_id,
            player_id=str(body.player_id),
            session_type=body.session_type,
            duration_minutes=body.duration_minutes,
            started_at=body.started_at,
            ended_at=body.ended_at,
            school_id=None if body.school_id is None else str(body.school_id),
            payment=payment,
            draw=draw,
            location_timezone=config.location_timezone,
        )
    except service.PlayerNotFound as exc:
        raise HTTPException(status_code=404, detail="Player not found") from exc
    except service.SessionScopeError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    except service.DrawRejected as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc

    return SessionOutModel(
        id=result.id,
        player_id=result.player_id,
        location_id=result.location_id,
        logged_by=result.logged_by,
        payment_id=result.payment_id,
        draw_entitlement_id=result.draw_entitlement_id,
        draw_remaining_units=result.draw_remaining_units,
    )
