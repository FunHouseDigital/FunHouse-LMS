"""Revenue HTTP endpoint (Req 11).

``GET /revenue/summary`` returns the three-stream revenue totals in integer
cents, restricted to the caller's scope for a manager/facilitator (Req 11.5).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from funhouse_api.db import get_connection
from funhouse_api.rbac import Scope, require_scope
from funhouse_api.revenue import reporter

router = APIRouter(tags=["revenue"])


class RevenueSummaryModel(BaseModel):
    """Three-stream revenue totals in integer cents (Req 11.1, 11.4)."""

    pay_per_use_cents: int
    subscription_cents: int
    school_contracts_cents: int


@router.get("/revenue/summary", response_model=RevenueSummaryModel)
def revenue_summary(
    scope: Scope = Depends(require_scope),
    conn: Any = Depends(get_connection),
) -> RevenueSummaryModel:
    """Return the scoped three-stream revenue summary (Req 11)."""
    if scope.role not in {"founder", "manager"}:
        raise HTTPException(status_code=403, detail="Forbidden")
    result = reporter.summary(conn, scope)
    return RevenueSummaryModel(
        pay_per_use_cents=result.pay_per_use_cents,
        subscription_cents=result.subscription_cents,
        school_contracts_cents=result.school_contracts_cents,
    )
