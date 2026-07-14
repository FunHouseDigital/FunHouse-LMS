"""Example tests for fail-closed authorization (Task 5.5).

When a caller's scope cannot be derived — an unknown role, or a
manager/facilitator missing the location/school its role requires — the request
is rejected rather than served (Req 7.6). This is verified at two levels:

* directly on :meth:`Scope.derive`, which raises :class:`AuthzError`; and
* end-to-end through the ``require_scope`` dependency on a minimal **test-only**
  protected route, which must respond ``403``.

The test route is mounted inside the test and never ships in the application.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from funhouse_api.app import create_app
from funhouse_api.auth.dependencies import Principal
from funhouse_api.auth.service import AuthUser, issue_token
from funhouse_api.config import ApiConfig
from funhouse_api.rbac import AuthzError, Scope, require_scope
from funhouse_pipeline.config import Config, DatabaseConfig

_CONFIG = ApiConfig(
    pipeline=Config(database=DatabaseConfig()),
    jwt_secret="rbac-unit-secret",
    jwt_ttl_seconds=3600,
)


def test_scope_derive_fails_closed_for_manager_without_location():
    principal = Principal(user_id="u", role="manager", location_id=None, school_id=None)
    with pytest.raises(AuthzError):
        Scope.derive(principal)


def test_scope_derive_fails_closed_for_facilitator_without_school():
    principal = Principal(user_id="u", role="facilitator", location_id="loc", school_id=None)
    with pytest.raises(AuthzError):
        Scope.derive(principal)


def test_scope_derive_fails_closed_for_unknown_role():
    principal = Principal(user_id="u", role="intruder", location_id="loc", school_id="s")
    with pytest.raises(AuthzError):
        Scope.derive(principal)


def _build_scoped_client() -> TestClient:
    """App with a test-only route guarded by require_scope (fail-closed)."""
    app = create_app(config=_CONFIG)

    @app.get("/_test_scoped")
    def _scoped(scope: Scope = Depends(require_scope)) -> dict[str, str]:
        return {"role": scope.role}

    return TestClient(app)


def test_require_scope_returns_403_when_scope_cannot_be_derived():
    """A manager token with no location scope is rejected with 403 (Req 7.6)."""
    client = _build_scoped_client()
    # A well-formed, correctly-signed token whose scope is nonetheless
    # underivable (manager without a location_id).
    token = issue_token(
        AuthUser(id="u1", role="manager", location_id=None, school_id=None),
        now=datetime.now(timezone.utc),
        secret=_CONFIG.jwt_secret,
        ttl_seconds=3600,
    )
    response = client.get("/_test_scoped", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
