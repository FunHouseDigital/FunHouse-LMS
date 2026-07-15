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

## Recommended merge order for the currently open PRs

The open PRs are largely independent, so any order works — but this order is the
cleanest, and the deployment PR must come **after** the application PRs:

1. **PR #3 — API `student_metrics` sync entity.** Backend that enables the PWA's
   live metrics.
2. **PR #2 — Revenue PWA + live metrics.** The client that consumes the above.
3. **PR #5 — facilitator `school_id` at login.** Additive API/auth change plus a
   migration.
4. **PR #4 — Deployment IaC + README + CI.** Merge after the app is in place; it
   carries the repo-wide README and CI.

> After all of these merge, `main` contains every component and CI runs all three
> jobs. Then perform the live deploy per
> [`docs/deployment-runbook.md`](docs/deployment-runbook.md).

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
tasks) before implementation. When you change behavior, keep the spec docs
updated additively.

## Deployment & gating note

The live AWS deploy is a founder-run step (see
[`docs/deployment-runbook.md`](docs/deployment-runbook.md)) and needs real
credentials. **Phase 2 (Lesson Engine, Bedrock) is gated behind Phase 1 field
acceptance** — do not begin Phase 2 work until the system is deployed and used in
the real lounge. All data at rest stays in `af-south-1` (POPIA).
