# FunHouse Container API image (Spec 2 / deploy Spec 3.5).
#
# Build context MUST be the repository root so both packages and the build
# backend inputs are available:
#
#     docker build -f funhouse_api/Dockerfile -t funhouse-api .
#
# The image installs ONLY runtime deps (no [dev] extras) and makes both
# funhouse_api and funhouse_pipeline importable (funhouse_api imports
# funhouse_pipeline). psycopg is pinned as psycopg[binary] in pyproject, which
# bundles libpq, so no OS-level libpq/build tools are required.

# --------------------------------------------------------------------------- #
# Stage 1: builder — install the project into an isolated virtualenv.
# --------------------------------------------------------------------------- #
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Isolated venv we can copy wholesale into the final stage.
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Build-backend inputs: pyproject + README (readme = "README.md") and BOTH
# packages (hatchling wheel packages = funhouse_pipeline, funhouse_api). The
# .sql migrations are force-included by hatchling and ship inside the wheel.
COPY pyproject.toml README.md ./
COPY funhouse_pipeline ./funhouse_pipeline
COPY funhouse_api ./funhouse_api

# Install runtime deps + the project (runtime deps only; no [dev] extras).
RUN pip install .

# --------------------------------------------------------------------------- #
# Stage 2: runtime — slim final image with only the venv + a non-root user.
# --------------------------------------------------------------------------- #
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Copy the fully-built virtualenv (funhouse_api + funhouse_pipeline installed).
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Flag-gated entrypoint: optionally runs idempotent migrations/seed on start
# (RUN_MIGRATIONS_ON_START / RUN_SEED_ON_START) then execs the CMD below. With
# the flags unset the behaviour is unchanged (it just runs uvicorn). Copied and
# made world-executable as root so the non-root appuser can run it.
COPY funhouse_api/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser
WORKDIR /home/appuser

# App Runner health check hits GET /health (no DB access required).
EXPOSE 8000

# The entrypoint performs optional migrate/seed then `exec "$@"` — so the CMD
# below is the server command it hands off to. create_app() is a factory in
# funhouse_api/app.py, so uvicorn must be told with --factory. Bind 0.0.0.0:8000
# (matches the IaC container_port / App Runner config). No --reload in production.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "funhouse_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
