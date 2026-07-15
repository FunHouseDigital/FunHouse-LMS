#!/bin/sh
# FunHouse Container API entrypoint (Spec 3.5 — one-command deploy).
#
# Flag-gated auto-migrate/seed on container start, then hand off to uvicorn.
#
#   RUN_MIGRATIONS_ON_START  when truthy (1/true/yes/on), run the idempotent
#                            schema migration (python -m
#                            funhouse_pipeline.db.apply_migrations) before the
#                            server starts. On failure the container exits
#                            non-zero so App Runner keeps the previous healthy
#                            version (no partial rollout).
#   RUN_SEED_ON_START        when truthy, additionally run the idempotent
#                            reference-data seed (python -m
#                            funhouse_pipeline.db.apply_seed) after migrations.
#
# Both operations are idempotent, so re-running (e.g. on every scale-up) is a
# safe no-op. When BOTH flags are unset/false the behaviour is unchanged: the
# entrypoint simply execs the server, so the original manual runbook path (run
# migrations from an in-VPC one-off, deploy the image with the flags off) still
# works exactly as before.
#
# POSIX sh only (no bashisms) — the base image ships /bin/sh (dash).

set -eu

# Truthy test: 1/true/yes/on (case-insensitive). Anything else is false.
is_truthy() {
	case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
	1 | true | yes | on) return 0 ;;
	*) return 1 ;;
	esac
}

if is_truthy "${RUN_MIGRATIONS_ON_START:-}"; then
	echo "[entrypoint] RUN_MIGRATIONS_ON_START set — applying database migrations..."
	# Fail the container on migration error (App Runner keeps the prior version).
	python -m funhouse_pipeline.db.apply_migrations
	echo "[entrypoint] migrations complete."
else
	echo "[entrypoint] RUN_MIGRATIONS_ON_START not set — skipping migrations."
fi

if is_truthy "${RUN_SEED_ON_START:-}"; then
	echo "[entrypoint] RUN_SEED_ON_START set — seeding reference data..."
	python -m funhouse_pipeline.db.apply_seed
	echo "[entrypoint] seed complete."
else
	echo "[entrypoint] RUN_SEED_ON_START not set — skipping seed."
fi

# Hand off to the ASGI server. "$@" is the Dockerfile CMD (the uvicorn command),
# so passing no CMD override still starts the server with the documented args.
echo "[entrypoint] starting API server: $*"
exec "$@"
