"""Example test for public-endpoint access without a token (Task 4.3).

Public endpoints (``/health`` and ``/auth/login``) omit the ``require_auth``
dependency and must be reachable without any ``Authorization`` header (Req 2.5).
``/auth/login`` still requires a valid body, so reaching it without a token is
demonstrated by getting a validation error (422) rather than an authentication
error (401) — proving the token gate is not what blocks the request.
"""

from __future__ import annotations

from tests.api_helpers import build_client


def test_health_is_public_without_token():
    """GET /health answers 200 with no Authorization header (Req 2.5)."""
    with build_client() as client:
        response = client.get("/health")
    assert response.status_code == 200


def test_login_is_public_without_token():
    """POST /auth/login is reachable without a token (Req 2.5).

    With an empty body it returns 422 (validation), NOT 401 — the endpoint is
    not behind the auth gate.
    """
    with build_client(connection=object()) as client:
        response = client.post("/auth/login", json={})
    assert response.status_code == 422
    assert response.status_code != 401
