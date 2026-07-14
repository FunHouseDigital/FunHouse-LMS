"""Sync HTTP endpoint: ``POST /sync`` (Req 4, 5).

Accepts a :class:`SyncBatch` of offline-created actions from a device and applies
them server-side via the :mod:`funhouse_api.sync.service`. Authenticated
(``require_auth``) and scoped (``require_scope``); the acting user (``logged_by``)
is the verified principal. Returns exactly one per-action result (Req 4.1); the
whole request succeeds (``200``) even when individual actions are ``rejected`` --
per-action isolation means a bad action never fails the batch (Req 4.5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from funhouse_api.auth.dependencies import Principal, require_auth
from funhouse_api.config import ApiConfig
from funhouse_api.db import get_connection
from funhouse_api.dependencies import get_api_config
from funhouse_api.rbac import Scope, require_scope
from funhouse_api.sync import service

router = APIRouter(tags=["sync"])


class SyncActionModel(BaseModel):
    """One offline-created write (design DTO ``SyncAction``)."""

    client_id: str
    entity: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class SyncBatchModel(BaseModel):
    """A batch of actions from one device (design DTO ``SyncBatch``)."""

    actions: list[SyncActionModel] = Field(default_factory=list)


class ActionResultModel(BaseModel):
    """The per-action result (design DTO ``ActionResult``)."""

    client_id: str
    entity: str
    status: str
    record_id: Any | None = None
    reason: str | None = None


class SyncResultModel(BaseModel):
    """The batch result (design DTO ``SyncResult``)."""

    results: list[ActionResultModel]


@router.post("/sync", response_model=SyncResultModel)
def sync_batch(
    body: SyncBatchModel,
    principal: Principal = Depends(require_auth),
    scope: Scope = Depends(require_scope),
    config: ApiConfig = Depends(get_api_config),
    conn: Any = Depends(get_connection),
) -> SyncResultModel:
    """Apply a batch of sync actions and return per-action results (Req 4, 5)."""
    actions = [
        service.SyncAction(
            client_id=a.client_id,
            entity=a.entity,
            created_at=a.created_at,
            payload=dict(a.payload),
        )
        for a in body.actions
    ]
    results = service.apply_batch(
        conn,
        scope,
        actions,
        logged_by=principal.user_id,
        location_timezone=config.location_timezone,
    )
    return SyncResultModel(
        results=[
            ActionResultModel(
                client_id=r.client_id,
                entity=r.entity,
                status=r.status,
                record_id=None if r.record_id is None else str(r.record_id),
                reason=r.reason,
            )
            for r in results
        ]
    )
