"""Property-based test for the Token_Verifier (Task 4.2).

Implements design Property 4. No database is required — ``require_auth`` only
verifies the JWT — so this runs in every environment. A minimal **test-only**
protected route is mounted onto the app (via the app factory) purely to exercise
``require_auth`` before the real resource routers exist; it is created inside the
test and never ships in the application.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from funhouse_api.app import create_app
from funhouse_api.auth.dependencies import Principal, require_auth
from funhouse_api.auth.service import AuthUser, issue_token
from funhouse_api.config import ApiConfig
from funhouse_pipeline.config import Config, DatabaseConfig

pytestmark = [pytest.mark.property]

_SETTINGS = settings(max_examples=100, deadline=None)

_CONFIG = ApiConfig(
    pipeline=Config(database=DatabaseConfig()),
    jwt_secret="token-verifier-secret",
    jwt_ttl_seconds=3600,
)
_WRONG_SECRET = "not-the-server-secret"
_USER = AuthUser(id="00000000-0000-0000-0000-000000000009", role="manager")


def _build_protected_client() -> TestClient:
    """Build an app with a test-only protected route guarded by require_auth."""
    app = create_app(config=_CONFIG)

    @app.get("/_test_protected")
    def _protected(principal: Principal = Depends(require_auth)) -> dict[str, str]:
        return {"user_id": principal.user_id}

    return TestClient(app)


# token "category" the generator chooses among.
_categories = st.sampled_from(["valid", "missing", "bad_signature", "expired"])


# Feature: funhouse-api, Property 4: Only valid unexpired tokens authenticate a
# protected request — for any protected endpoint and any missing/invalid-
# signature/expired token the request is rejected; a well-formed unexpired token
# signed with the server secret is accepted.
# Validates: Requirements 2.2, 2.3, 2.6, 2.7
@_SETTINGS
@given(category=_categories)
def test_property_4_only_valid_unexpired_tokens_authenticate(category: str) -> None:
    client = _build_protected_client()
    now = datetime.now(timezone.utc)

    headers: dict[str, str] = {}
    if category == "valid":
        token = issue_token(_USER, now=now, secret=_CONFIG.jwt_secret, ttl_seconds=3600)
        headers = {"Authorization": f"Bearer {token}"}
    elif category == "bad_signature":
        token = issue_token(_USER, now=now, secret=_WRONG_SECRET, ttl_seconds=3600)
        headers = {"Authorization": f"Bearer {token}"}
    elif category == "expired":
        past = now - timedelta(seconds=7200)
        token = issue_token(_USER, now=past, secret=_CONFIG.jwt_secret, ttl_seconds=1)
        headers = {"Authorization": f"Bearer {token}"}
    # category == "missing" → send no Authorization header.

    response = client.get("/_test_protected", headers=headers)

    if category == "valid":
        assert response.status_code == 200
        assert response.json() == {"user_id": _USER.id}
    else:
        assert response.status_code == 401
