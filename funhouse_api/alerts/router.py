"""Alerts HTTP endpoint (Req 12).

``GET /alerts`` returns the deterministic, rule-based operational alerts within
the caller's scope (Req 12.6).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from funhouse_api.alerts import engine
from funhouse_api.config import ApiConfig
from funhouse_api.db import get_connection
from funhouse_api.dependencies import get_api_config
from funhouse_api.rbac import Scope, require_scope

router = APIRouter(tags=["alerts"])


class AlertModel(BaseModel):
    """A single deterministic alert (Req 12)."""

    type: str
    subject_id: str
    detail: str = ""


@router.get("/alerts", response_model=list[AlertModel])
def list_alerts(
    scope: Scope = Depends(require_scope),
    config: ApiConfig = Depends(get_api_config),
    conn: Any = Depends(get_connection),
) -> list[AlertModel]:
    """Return deterministic operational alerts within scope (Req 12)."""
    if scope.role not in {"founder", "manager"}:
        raise HTTPException(status_code=403, detail="Forbidden")
    found = engine.alerts(
        conn,
        scope,
        now=datetime.now(timezone.utc),
        expiry_horizon_days=config.alert_expiry_horizon_days,
    )
    return [AlertModel(type=a.type, subject_id=a.subject_id, detail=a.detail) for a in found]
