"""Shared FastAPI dependencies for the FunHouse Container API (Spec 2).

Currently exposes :func:`get_api_config`, which resolves the :class:`ApiConfig`
stored on ``app.state.config`` by the app factory. Keeping config access behind
a dependency lets routers and the auth/RBAC layers read the JWT secret, token
lifetime, and scope settings without re-loading configuration from the
environment on every request.
"""

from __future__ import annotations

from fastapi import Request

from funhouse_api.config import ApiConfig, load_api_config


def get_api_config(request: Request) -> ApiConfig:
    """Return the :class:`ApiConfig` for the running app.

    Reads the config the app factory placed on ``app.state.config``; falls back
    to loading from the environment if it is somehow absent (e.g. an app built
    without the factory).
    """
    config = getattr(request.app.state, "config", None)
    if config is None:
        config = load_api_config()
    return config
