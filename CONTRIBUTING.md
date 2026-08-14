# Contributing to FunHouse

This is the **FunHouse Operating System** monorepo — the data pipeline, container
API, offline-first PWA, and infrastructure that run FunHouse Digital. All
contributions land through **feature branches and pull requests against `main`**.
Never push directly to `main`: it is the integration branch and must always stay
green and deployable.

## Repository layout

- **`funhouse_pipeline/`** — Phase 0 ETL and the 14-table PostgreSQL schema.
  Idempotent migrations live in `funhouse_pipeline/sql/*.sql` and are applied via
  `run_migrations`, followed by seed data.
- **`funhouse_api/`** — the FastAPI Container API (auth, RBAC, entitlements, and
  the sync target for the PWA). Ships as a single Docker container and is
  Postgres-only.
- **`web/`** — the offline-first Revenue PWA (React/Vite, IndexedDB, service
  worker) that syncs to the API.
- **`infra/`** — Terraform IaC for AWS `af-south-1` (VPC, RDS, App Runner,
  S3 + CloudFront, SSM).
- **`docs/`** — deployment runbook, smoke-test checklist, and the pipeline
  command reference.
- **`tests/`** — the pytest suite (pipeline + API), including Hypothesis
  property tests.
- **`.kiro/specs/`** — the spec-driven specs (requirements / design / tasks) each
  feature was built from.

## Branch & PR conventions

- Name feature branches `feature/<slug>`; use `bugfix/<slug>` and `docs/<slug>`
  prefixes where appropriate.
- Open a PR against `main`. Keep each PR scoped to one component or concern where
  possible.
- PRs must be **green in CI** before merge.
- Never force-push `main`.

## Release-sensitive changes

Keep pull requests independently deployable and order coupled schema/API work
explicitly. Additive database migrations must merge and be applied before any
API revision that depends on the new schema. The API and PWA deploy through
separate Vercel projects, so confirm both production deployments when a change
crosses that boundary. Rerun the applicable live verification workflow from
`main`, record its `head_sha`, and require it to match the corresponding Vercel
Production deployment SHA before manual smoke checks. A green CI run or
`/health` response alone is not production acceptance.

## How CI works (`.github/workflows/ci.yml`)

CI has three jobs, each **guarded on component presence** so branches that don't
touch a component skip its job green:

- **`infra-gates`** — terraform fmt/validate, shellcheck, and residency /
  no-forbidden assertions.
- **`python-tests`** — pytest against a PostgreSQL service (DB-backed property
  tests execute here).
- **`web-tests`** — `npm test` plus build.

Each job self-skips (green) when its component isn't present on the branch, and
all three enforce on `main`. **No AWS credentials are used in CI.**

## Local development

**Python**

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
pytest
```

DB-backed tests need a reachable PostgreSQL via the `DB_*` env vars /
`FUNHOUSE_TEST_DSN`, and skip cleanly when none is available.

**Web**

```bash
cd web && npm ci && npm run test && npm run build
```

**Infra** (offline, no AWS)

```bash
cd infra
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

Dry-run the assertion scripts against `scripts/fixtures/`.

## Commit messages

Use a short imperative subject, optionally prefixed with `component: summary`
(e.g. `funhouse-api: …`). Add a concise body explaining the change and how it was
tested.

## Spec-driven workflow

Features are specced under `.kiro/specs/<feature>/` (requirements → design →
tasks) before implementation. When you change behaviour, keep the spec docs
updated additively.

## Deployment and gating note

Current production uses separate Vercel projects for the API and PWA, backed by
Supabase. Follow [`docs/deployment-runbook.md`](docs/deployment-runbook.md),
correlate the live verification run's `head_sha` with the Vercel Production
deployment SHA, complete two protected browser smoke runs for the same candidate
SHA, and then record the synthetic-only
[Phase 1 field-acceptance rehearsal](docs/field-acceptance-checklist.md) on the
actual lounge device. The repository does not establish the selected
Vercel/Supabase storage regions or provider terms, so do not claim
`af-south-1` residency for that path without separate evidence.

The browser-only GitHub **Deploy** workflow documented in
[`docs/deploy-from-github.md`](docs/deploy-from-github.md) is the optional future
AWS full-stack path and needs live AWS credentials. **Phase 2 (Lesson Engine,
Bedrock) remains gated behind a recorded Phase 1 field-acceptance GO** — do not
begin Phase 2 work until the candidate release passes the same-SHA API and
browser gates and the accepted device completes the real-lounge rehearsal.
