"""Integration tests for the API test harness and the public health endpoint.

These verify the FastAPI wiring (app factory -> router -> TestClient) and the
public liveness endpoint (Req 2.5). They require no database, so they run in
every environment.
"""

from __future__ import annotations

from tests.api_helpers import build_client


def test_health_endpoint_returns_200_and_ok_status():
    """GET /health returns 200 with a liveness payload, no JWT required (Req 2.5)."""
    with build_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_is_public_no_authorization_header_needed():
    """The health endpoint is reachable without any Authorization header (Req 2.5)."""
    with build_client() as client:
        # Explicitly send no auth header; a public endpoint must still answer 200.
        response = client.get("/health", headers={})

    assert response.status_code == 200


def test_create_app_registers_health_route():
    """create_app() wires the /health route into the application (Task 1.3)."""
    from funhouse_api.app import create_app

    app = create_app()
    # The OpenAPI schema resolves included routers robustly across FastAPI
    # versions (some versions register routers lazily on startup).
    assert "/health" in app.openapi()["paths"]
