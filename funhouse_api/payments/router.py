"""Payment + product HTTP endpoints (Req 10).

Thin FastAPI router over :mod:`funhouse_api.payments.service`. Every endpoint is
authenticated and scoped; ``logged_by`` is taken from the verified principal.

* ``POST /payments`` -- record a payment. ``amount_cents`` is required -> ``422``
  if omitted (Req 10.5). A cross-scope player -> ``403`` (Req 3.5).
* ``GET /products`` -- read the seeded catalog within scope (Req 10.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from funhouse_api.auth.dependencies import Principal, require_auth
from funhouse_api.db import get_connection
from funhouse_api.payments import service
from funhouse_api.rbac import Scope, require_scope

router = APIRouter(tags=["payments"])


class PaymentCreate(BaseModel):
    """Payment payload. ``amount_cents`` is required -> 422 if omitted (Req 10.5)."""

    player_id: UUID
    amount_cents: int
    product_id: UUID | None = None
    method: str | None = None
    paid_at: datetime | None = None


class PaymentOutModel(BaseModel):
    """A recorded payment."""

    id: UUID
    player_id: UUID
    product_id: UUID | None
    amount_cents: int
    method: str | None
    location_id: UUID


class ProductOutModel(BaseModel):
    """A seeded product within scope (Req 10.3)."""

    id: UUID
    name: str
    type: str
    price_cents: int
    rules: dict[str, Any]
    location_id: UUID


@router.post("/payments", response_model=PaymentOutModel, status_code=201)
def record_payment(
    body: PaymentCreate,
    principal: Principal = Depends(require_auth),
    scope: Scope = Depends(require_scope),
    conn: Any = Depends(get_connection),
) -> PaymentOutModel:
    """Record a payment against a player within scope (Req 10.1, 10.2, 10.4)."""
    try:
        payment = service.record_payment(
            conn,
            scope,
            logged_by=principal.user_id,
            player_id=str(body.player_id),
            amount_cents=body.amount_cents,
            product_id=None if body.product_id is None else str(body.product_id),
            method=body.method,
            paid_at=body.paid_at,
        )
    except service.PlayerNotFound as exc:
        raise HTTPException(status_code=404, detail="Player not found") from exc
    except service.PaymentScopeError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    return PaymentOutModel(
        id=payment.id,
        player_id=payment.player_id,
        product_id=payment.product_id,
        amount_cents=payment.amount_cents,
        method=payment.method,
        location_id=payment.location_id,
    )


@router.get("/products", response_model=list[ProductOutModel])
def list_products(
    scope: Scope = Depends(require_scope),
    conn: Any = Depends(get_connection),
) -> list[ProductOutModel]:
    """Return the seeded product catalog within scope (Req 10.3)."""
    return [
        ProductOutModel(
            id=p.id,
            name=p.name,
            type=p.type,
            price_cents=p.price_cents,
            rules=p.rules,
            location_id=p.location_id,
        )
        for p in service.list_products(conn, scope)
    ]
