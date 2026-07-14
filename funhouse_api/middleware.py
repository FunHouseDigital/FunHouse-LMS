"""Cross-cutting HTTP middleware (Req 14.3, 14.7).

TLS is terminated at the edge (App Runner / ALB / reverse proxy), which is the
deployment expectation (Req 14.3). As an in-app backstop, when the deployment
marks TLS as required (``ApiConfig.tls_required``) this middleware inspects the
forwarded protocol (``X-Forwarded-Proto``, set by the terminating proxy) and
**rejects** any request that did not arrive over HTTPS rather than serving it
over an unencrypted connection (Req 14.7).

When TLS is not marked required (the default for local/dev), the middleware is a
no-op, so tests and local runs over plain HTTP are unaffected.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

#: The proxy-set header carrying the original request scheme.
FORWARDED_PROTO_HEADER = "x-forwarded-proto"


def _is_https(request: Request) -> bool:
    """Return True when the request reached the edge over HTTPS.

    Prefers the proxy-set ``X-Forwarded-Proto`` (the first value when a
    comma-separated chain is present); falls back to the ASGI scheme when no
    forwarded header is present (e.g. a direct TLS listener).
    """
    forwarded = request.headers.get(FORWARDED_PROTO_HEADER)
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


class TLSRequiredMiddleware(BaseHTTPMiddleware):
    """Reject non-HTTPS requests when the deployment requires TLS (Req 14.7)."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        config = getattr(request.app.state, "config", None)
        tls_required = bool(getattr(config, "tls_required", False))
        if tls_required and not _is_https(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "TLS required"},
            )
        return await call_next(request)
