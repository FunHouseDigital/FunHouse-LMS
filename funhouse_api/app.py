"""FastAPI application factory for the FunHouse Container API (Spec 2).

``create_app()`` builds and returns a configured :class:`fastapi.FastAPI`
instance. Routers are registered here as they are implemented; at this stage the
factory wires the public ``/health`` endpoint. Later tasks add auth, RBAC,
resource, sync, and cross-cutting routers/middleware.

The factory is the single composition root: tests build the app via
``TestClient(create_app())`` and override the ``get_connection`` dependency to
run against a disposable schema.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from funhouse_api import health
from funhouse_api.alerts import router as alerts_router
from funhouse_api.auth import router as auth_router
from funhouse_api.config import ApiConfig, load_api_config
from funhouse_api.entitlements import router as entitlements_router
from funhouse_api.middleware import TLSRequiredMiddleware
from funhouse_api.payments import router as payments_router
from funhouse_api.players import router as players_router
from funhouse_api.revenue import router as revenue_router
from funhouse_api.sessions import router as sessions_router
from funhouse_api.sync import router as sync_router


def create_app(config: ApiConfig | None = None) -> FastAPI:
    """Create and configure the FunHouse API application.

    Args:
        config: Optional pre-loaded :class:`ApiConfig`. When omitted, the
            configuration is loaded from the environment. Stored on
            ``app.state.config`` for dependencies that need it.

    Returns:
        A configured FastAPI application with all currently-implemented routers
        registered.
    """
    app = FastAPI(
        title="FunHouse Container API",
        version="0.1.0",
        description=(
            "Single portable HTTP API for the FunHouse Operating System, built on "
            "the Phase 0 PostgreSQL data foundation. PostgreSQL is the only "
            "persistence dependency; authentication is self-managed (JWT + bcrypt)."
        ),
    )

    resolved_config = config if config is not None else load_api_config()
    app.state.config = resolved_config

    if resolved_config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved_config.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # TLS backstop: reject non-HTTPS requests when the deployment requires TLS
    # (Req 14.3, 14.7). No-op when tls_required is false (local/dev, tests).
    app.add_middleware(TLSRequiredMiddleware)

    # Public endpoints (no JWT). Additional routers are registered by later tasks.
    app.include_router(health.router)
    app.include_router(auth_router.router)

    # Protected resource routers.
    app.include_router(entitlements_router.router)
    app.include_router(players_router.router)
    app.include_router(sessions_router.router)
    app.include_router(payments_router.router)
    app.include_router(revenue_router.router)
    app.include_router(alerts_router.router)
    app.include_router(sync_router.router)

    return app
