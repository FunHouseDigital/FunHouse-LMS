"""Vercel-compatible ASGI entrypoint for the FunHouse API."""

from funhouse_api.app import create_app

app = create_app()
