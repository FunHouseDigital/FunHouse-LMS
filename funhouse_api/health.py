"""Public liveness endpoint (Req 2.5).

``GET /health`` is a public endpoint: the Token_Verifier allows it without a
JWT. It performs no database access so it can answer liveness even while the
database is unavailable.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return a simple liveness payload (public, no JWT required)."""
    return {"status": "ok"}
