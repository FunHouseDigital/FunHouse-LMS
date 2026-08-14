# FunHouse Deployment Runbook (Spec 3.5)

> **Zero-local-tooling AWS deploy:** see [`docs/deploy-from-github.md`](deploy-from-github.md)
> to deploy the whole stack from the browser via the **Deploy** GitHub Actions
> workflow (Actions → Deploy → Run workflow) — no local AWS CLI, Terraform,
> Docker, or Node.js required.


## Operate the current Vercel + Supabase production path

The Container API and Revenue PWA are deployed as separate Vercel projects from
this repository. The API project uses the repository root at
`https://fun-house-lms.vercel.app` and connects to Supabase. The PWA project
uses `web` as its root directory. Migration 010 and the dedicated Supabase
runtime-role verification completed before the client-identity API revision was
merged.

Deployment success is not release acceptance. For every candidate release,
**Verify Live API Role Access** must pass against the exact current SHA, then
**Verify Live PWA Browser** must pass twice for that same SHA (the second stable-
identity run proves replay safety), and the
[Phase 1 field-acceptance rehearsal](field-acceptance-checklist.md) must be
recorded on the actual lounge device before real learner data is used. Record
the operator-approved stable PWA origin rather than inferring it from a
transient deployment URL. AWS remains the optional future full-stack path
documented below.

### A. Recreate or replace the PWA project **[FOUNDER-RUN / VERCEL ACCOUNT]**

The production PWA project already exists. Use this section only when creating a
replacement project or a new environment:

1. Open the [Vercel dashboard](https://vercel.com/dashboard), choose **Add New →
   Project**, and import `FunHouseDigital/FunHouse-LMS` again. Do not modify or
   unlink the existing API project.
2. Use a distinct project name such as `funhouse-revenue-pwa` and configure:

   | Setting | Value |
   | --- | --- |
   | Production branch | `main` |
   | Root Directory | `web` |
   | Framework Preset | `Vite` |
   | Build Command | `npm run build` |
   | Output Directory | `dist` |

3. Before deploying, add this **Production** environment variable to the new
   PWA project:

   ```text
   VITE_API_BASE_URL=https://fun-house-lms.vercel.app
   ```

   This is a Vite build-time value. Adding or changing it requires a new PWA
   deployment. Do not point production builds at a preview API hostname. The
   build also consumes Vercel's non-secret `VERCEL_GIT_COMMIT_SHA` system value
   and fails closed when Vercel cannot supply a 40-character Git SHA; do not
   replace it with a manually maintained release variable. The resulting app
   displays the first seven characters as **Release `<short-sha>`** so an
   operator can identify the shell actually running on a device.
4. Deploy `main`, wait for **Ready**, and record the generated HTTPS origin as
   `PWA_ORIGIN`, for example `https://funhouse-revenue-pwa.vercel.app`. An
   origin contains only scheme and hostname: no path and no trailing slash.

### B. Allow the PWA origin on the API **[FOUNDER-RUN / VERCEL ACCOUNT]**

1. In the existing API project, open **Environment Variables** and set
   `FUNHOUSE_CORS_ORIGINS` for **Production** to the exact `PWA_ORIGIN`.
   If approved origins already exist, keep them and append the new origin with
   a comma. Never use `*` because browser credentials are enabled.
2. Redeploy the existing API project from the reviewed `main` commit. Do not
   change its `DB_*`, `JWT_*`, Supabase, or TLS variables.
3. Verify API health and the exact browser preflight before signing in:

   ```bash
   API_ORIGIN=https://fun-house-lms.vercel.app
   PWA_ORIGIN=https://<pwa-project>.vercel.app

   curl -fsS "$API_ORIGIN/health"
   curl -i -X OPTIONS "$API_ORIGIN/auth/login" \
     -H "Origin: $PWA_ORIGIN" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: content-type"
   ```

   Expected: health returns `200`; preflight returns `200` with
   `access-control-allow-origin: <PWA_ORIGIN>`.
4. Run **Verify Live API Role Access** from `main` after the API redeploy.
   Record the workflow run's `head_sha` and the API's Vercel Production
   deployment SHA; they must match. The workflow must pass before browser
   acceptance.

### C. Verify static/PWA delivery

```bash
PWA_ORIGIN=https://<pwa-project>.vercel.app
curl -fsSI "$PWA_ORIGIN/"
curl -fsSI "$PWA_ORIGIN/login"               # SPA deep-link fallback
curl -fsSI "$PWA_ORIGIN/manifest.webmanifest"
curl -fsSI "$PWA_ORIGIN/sw.js"

ASSET_PATH="$(curl -fsS "$PWA_ORIGIN/" \
  | grep -oE '/assets/[^" ]+\.(js|css)' | head -n 1)"
test -n "$ASSET_PATH"
curl -fsSI "$PWA_ORIGIN$ASSET_PATH"
```

Expected: every request succeeds over HTTPS. The app shell, deep-link fallback,
`sw.js`, and manifest return `Cache-Control: public, max-age=0,
must-revalidate`. The content-hashed asset returns `Cache-Control: public,
max-age=31536000, immutable`. Complete the browser-only checks in
[`docs/smoke-test-checklist.md`](smoke-test-checklist.md), then complete the
synthetic-only
[Phase 1 field-acceptance rehearsal](field-acceptance-checklist.md) on the
actual lounge device.

### Staff login secrets and Loyiso rotation **[FOUNDER-RUN / GITHUB ACCOUNT]**

Production keeps separate protected GitHub environment secrets for each seeded
staff credential:

| Seeded login | Role | GitHub `production` environment secret |
| --- | --- | --- |
| `Aya` | founder | `BOOTSTRAP_USER_PASSWORD` |
| `Loyiso` | manager | `LOYISO_BOOTSTRAP_PASSWORD` |
| `Facilitator` | facilitator | `FACILITATOR_BOOTSTRAP_PASSWORD` |

Never paste these values into workflow inputs, issues, logs, SQL, screenshots,
or chat. Generate and retain each value in the approved password manager. A
password must be a single line containing at least 12 characters and at most 72
UTF-8 bytes.

To establish or change Loyiso's password without changing Aya:

1. In GitHub, open **Settings → Environments → production → Environment
   secrets** and create or replace `LOYISO_BOOTSTRAP_PASSWORD` with the new
   password-manager value.
2. From the `main` branch, run **Rotate Live Loyiso Password** with confirmation
   `rotate-live-loyiso-password`. The serialized workflow validates the exact
   seeded `Loyiso` row, manager role, Smithfield scope, and uniqueness under
   table and row locks before replacing only its bcrypt hash.
3. Require the workflow's live login probe to pass. Then run **Verify Live API
   Role Access**, which reads the three distinct protected secrets and verifies
   founder, manager, and facilitator access.
4. Sign in to the PWA as `Loyiso` using the password-manager value and complete
   the offline smoke test. Do not share the password with the deployment
   operator.

The normal **Initialize Live Supabase Database** workflow remains
initialisation-only and refuses implicit rotation. Selecting `Loyiso` there
uses `LOYISO_BOOTSTRAP_PASSWORD`; it never falls back to Aya's secret. Password
rotation does not revoke already-issued JWTs, which remain valid until expiry.

### Vercel rollback and custom-domain rules

- A failed update to the established PWA can be rolled back by promoting its
  previous working deployment in Vercel.
- When creating a replacement project or new environment, there may be no
  previous PWA deployment. Keep its generated hostname unpublished until
  acceptance passes. If it fails, remove any custom alias or disable/delete the
  replacement project, remove its exact origin from `FUNHOUSE_CORS_ORIGINS`, and
  redeploy the API only when that CORS cleanup is required. Leave the established
  API and PWA deployments otherwise untouched.
- If the API redeploy fails, restore its previous deployment immediately. If
  only CORS fails, correct `FUNHOUSE_CORS_ORIGINS` and redeploy; do not alter DB
  credentials.
- Add a custom PWA domain only after the generated Vercel hostname passes. Add
  the custom HTTPS origin to `FUNHOUSE_CORS_ORIGINS` **before** directing users
  to it, while retaining the generated origin until cutover is verified.
- Preview PWA URLs are not production origins and are intentionally not
  wildcarded into CORS.

---

## Future full-stack AWS deployment

The ordered, end-to-end procedure below stands up and operates the FunHouse
production deployment on AWS **af-south-1**, strictly within PRD §3.1. It is
retained so AWS can become the full production platform later. It is written so
a **non-founder operator** can follow it without tribal knowledge (Req 9.5).

> **Live-cloud steps are founder-run.** Steps that touch a real AWS account
> (marked **[FOUNDER-RUN / LIVE CLOUD]**) require live AWS credentials and are
> executed by the founder/operator, not committed automation. The reproducible
> artifacts (Terraform, this runbook, the smoke-test checklist, the assertion
> scripts, and the migration shim) live in the repo; this document is the
> procedure to run them.

## Automated AWS deploy

For a hands-off first bring-up (or a re-deploy), run the single Windows
PowerShell script `scripts/deploy.ps1` from the repo root. It performs the whole
sequence below — preflight, state bucket, secrets, ECR build/push, Terraform
apply, PWA build/publish, and the CORS second apply — and is idempotent, so it
is safe to re-run.

**Prerequisites**

- AWS CLI v2, **Terraform ≥ 1.10**, and **Docker Desktop running** on PATH.
- **Node.js + npm** on PATH for the PWA build step (optional — if absent the
  script prints the exact manual PWA commands and continues).
- An authenticated AWS session for af-south-1:

  ```powershell
  aws sso login --profile funhouse
  ```

**Run it**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy.ps1
# optional overrides:
#   -Location loc1  -Region af-south-1  -Profile funhouse
```

At the end it prints the **CloudFront URL** (open this) and the **App Runner
URL**, then points you at `docs/smoke-test-checklist.md`.

> **This replaces the manual in-VPC one-off migration (Step 5).** On its first
> apply the script sets `run_migrations_on_start=true` and
> `run_seed_on_start=true`, so App Runner injects `RUN_MIGRATIONS_ON_START` /
> `RUN_SEED_ON_START` and the container applies the (idempotent) schema and
> reference seed itself on start-up. If migrations fail the container exits
> non-zero and App Runner keeps the previous healthy version. The standing
> Terraform default for both flags is `false`, so the manual path below still
> works unchanged when you deploy an image without them.

A POSIX bash equivalent, `scripts/deploy.sh`, is provided for non-Windows
operators; PowerShell is the supported path for the founder.

The **manual, step-by-step procedure below remains the fallback** — use it when
you need to run an individual phase by hand, or to understand exactly what the
script automates.

---

## Locked decisions

- **Schema migration** runs from an **ephemeral in-VPC one-off** that reaches
  the private RDS, then is torn down — **no** public RDS exposure, **no**
  standing service (Design C6, Open Question 1 recommended).
- **KMS**: AWS-managed keys (`aws/rds`, `aws/ssm`) — no dedicated CMKs (Design
  Open Question 2 recommended).

## Conventions

- Commands are run from `infra/` unless noted.
- `LOC=loc1` for Location 1; substitute `loc2` (with its var-files) for a second
  location — the procedure is identical (Req 8.4).
- Placeholders like `<apprunner_url>` are Terraform outputs captured in Step 3.

---

## 0. Prerequisites  **[FOUNDER-RUN / LIVE CLOUD for credentials]**

- Terraform **≥ 1.10** installed (`terraform version`).
- AWS credentials for an af-south-1-enabled account (`aws sts get-caller-identity`).
- Docker installed and running (to build/push the API image).
- A built PWA bundle produced later in Step 6 (`web/dist/`), and the API
  Dockerfile in `funhouse_api/`.
- `jq` installed (for the verification assertion scripts).

One-time: create the versioned+encrypted **S3 state bucket** in af-south-1 — see
the "One-time state-bucket bootstrap" section of `infra/README.md`.

## 1. Seed secrets into SSM (values never committed)  **[FOUNDER-RUN / LIVE CLOUD]** — Req 9.3

The secret **values** are supplied to Terraform from an **uncommitted** var-file
matched by `*.secrets.tfvars` in `.gitignore` (Req 5.2). Generate strong values
and write them locally only:

```bash
# infra/loc1.secrets.tfvars  — DO NOT COMMIT
cat > loc1.secrets.tfvars <<EOF
db_master_username = "funhouse_admin"
db_master_password = "$(openssl rand -base64 24)"
jwt_secret         = "$(openssl rand -base64 48)"
EOF
chmod 600 loc1.secrets.tfvars
```

Terraform writes these into SSM **SecureString** parameters (`/funhouse/loc1/db/password`,
`/db/user`, `/jwt/secret`) during `apply`; App Runner reads them by ARN at
runtime. No secret literal is ever committed to the repo.

## 2. Initialize Terraform  **[FOUNDER-RUN / LIVE CLOUD]**

```bash
terraform init            # configures the S3 remote-state backend
```

## 3. Provision infrastructure (first apply)  **[FOUNDER-RUN / LIVE CLOUD]** — Req 9.2

```bash
terraform plan  -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars -out=loc1.tfplan
# (optional offline gate before applying — see Step 9 for the assertion scripts)
terraform apply loc1.tfplan
```

Capture the outputs — you will need them below:

```bash
terraform output      # apprunner_url, cloudfront_domain, cloudfront_origin,
                      # cloudfront_distribution_id, web_bucket_name, rds_endpoint
```

Expected: RDS instance, App Runner service, S3 bucket + CloudFront distribution,
SSM parameters, VPC + connector all created in af-south-1. (On this first apply
`FUNHOUSE_CORS_ORIGINS` is empty; it is set in Step 7.)

## 4. Publish the Container_API image to ECR  **[FOUNDER-RUN / LIVE CLOUD]** — Req 9.2

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=af-south-1
REPO=funhouse-api
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
docker build -t $REPO:latest -f funhouse_api/Dockerfile .
docker tag  $REPO:latest $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
```

App Runner deploys the pushed image. Confirm the service reaches **RUNNING**
and `/health` returns 200 over HTTPS:

```bash
curl -fsS https://<apprunner_url>/health      # expect: 200 OK
```

## 5. Apply the schema via the ephemeral in-VPC one-off  **[FOUNDER-RUN / LIVE CLOUD]** — Req 3.3, 9.2

The schema is applied by running the repository's own idempotent
`run_migrations`, **never** hand-authored SQL (Req 3.1). Because RDS is private,
run it from a short-lived context **inside the VPC**, then tear it down.

**Recommended: an ephemeral in-VPC one-off** (e.g. a throwaway CodeBuild/ECS-run
task or a temporary SSM-managed instance in one of the two private subnets, in
the connector security group). On that context:

```bash
# Credentials come from SSM (never typed inline); TLS is required.
export DB_HOST=$(aws ssm get-parameter --name /funhouse/loc1/db/host \
                  --query Parameter.Value --output text)
export DB_NAME=$(aws ssm get-parameter --name /funhouse/loc1/db/name \
                  --query Parameter.Value --output text)
export DB_USER=$(aws ssm get-parameter --name /funhouse/loc1/db/user \
                  --with-decryption --query Parameter.Value --output text)
export DB_PASSWORD=$(aws ssm get-parameter --name /funhouse/loc1/db/password \
                  --with-decryption --query Parameter.Value --output text)
export DB_PORT=5432
export DB_SSLMODE=require        # TLS in transit to RDS (Req 4.6, 7.2)

python -m funhouse_pipeline.db.apply_migrations
```

Expected output — the created / already-present report, e.g.:

```
Applying migrations to <host>:5432/funhouse (sslmode=require)
Applied migrations: 001_schema.sql, 002_consents_append_only.sql, 003_role_facilitator.sql, 004_users_school_id.sql, 005_public_schema_lockdown.sql, 006_consents_function_search_path.sql, 007_runtime_role_access.sql, 008_sessions_reference.sql, 009_holiday_special_price.sql, 010_sync_log_client_id.sql
  Created: locations, schools, users, players, guardians, consents, products, entitlements, sessions, attendance, payments, lessons, student_metrics, sync_log
  Already present: (none)
```

Re-running is a safe no-op (idempotent): a second run reports `Created: (none)`
and lists the tables under `Already present` (Req 3.2).

Migration `009_holiday_special_price.sql` updates the existing Holiday Special
catalog row in place to `price_cents = 25000` (R250.00). It does not replace the
product or rewrite historical payment amounts or entitlements. After applying
it in production, every active Revenue PWA sales device must be online and use
**Refresh data** (or sign out and back in), then wait for the updated reference-
data timestamp before recording a Holiday Special sale. This replaces any
cached R0 catalog entry with the approved price.

Migration `010_sync_log_client_id.sql` adds a nullable, unique `client_id` to
`sync_log` and marks pre-migration audit rows for safe transition matching.
Existing audit history remains unchanged. New offline entitlement draws use the
stable action identity as their atomic idempotency receipt, so replaying the same
action cannot decrement a balance twice, while separate actions created at the
same time remain distinct.

Migration 010 was applied to live Supabase and verified before the API revision
that writes stable sync action receipts was merged. Preserve that schema-first
order when creating a new environment or recovering an older database: apply
and verify migration 010 before deploying the dependent API revision. During a
rolling deployment, old API instances omit the new columns and their draw
receipts inherit the temporary legacy marker; the new revision writes `FALSE`
explicitly for direct/null-identity audits. Keep the database default at `TRUE`
until rollback to every old API binary is formally retired. Offline entitlement
create/draw actions require the new column. On a large `sync_log`, schedule and
monitor the migration because the unique-index build temporarily blocks writes
to that table.

### Migrations 005-007 security contract and Supabase rollout

Migration `005_public_schema_lockdown.sql` first establishes a fail-closed
baseline for the FunHouse schema. Every replay removes existing RLS policies
from the 14 FunHouse tables, enables non-forced RLS, and removes direct object
privileges from `PUBLIC`, `anon`, `authenticated`, `service_role`, and
`authenticator`. Migration `007_runtime_role_access.sql` then restores only the
reviewed policies and table privileges for the dedicated `funhouse_runtime`
login in the same transaction. Do not add a Supabase REST policy out of band: a
later migration run will remove it. Any future design that uses Supabase REST
must explicitly supersede migrations 005 and 007 with a reviewed policy model.

Both migrations require every FunHouse table to be owned by the active
maintenance role. The **Initialize Live Supabase Database** workflow remains an
owner/migrator-only operation and can optionally set
`DB_MAINTENANCE_ROLE=funhouse_owner`; Vercel must use the separate
`funhouse_runtime.<project-ref>` session-pooler identity after cutover. Never
store the owner or migrator password in Vercel.

The migration checks effective privileges through inherited and `SET ROLE`
membership paths. If it reports that a Data API role can reach a privileged
role, remove that unexpected membership/grant and rerun; the failed migration
is rolled back atomically. It also revokes PostgreSQL's built-in `PUBLIC EXECUTE`
default for every future function created by the migration role. PostgreSQL
cannot scope that built-in default revoke to one schema. Existing functions in
other schemas are unchanged.

Migration `006_consents_function_search_path.sql` fixes the consent trigger
function's search path to the empty string. This prevents caller-controlled
schemas from changing unqualified name resolution while preserving the existing
function body, owner, privileges, and trigger. PostgreSQL continues to resolve
`pg_catalog` implicitly. Future edits to this function must schema-qualify any
database objects they reference.

Migration `007_runtime_role_access.sql` is a compatibility no-op on deployment
targets where `funhouse_runtime` does not exist. The controlled first migration
sets `funhouse.enable_runtime_role=on` so a missing role fails closed. Once the
role exists, migration 007 restores its full grant/policy contract on every
replay even if that setting is omitted, preventing migration 005's policy reset
from taking the runtime offline. The role must be `LOGIN NOSUPERUSER NOCREATEDB
NOCREATEROLE NOREPLICATION NOBYPASSRLS`
with no role memberships in either direction. It grants only the table
operations used by FastAPI, creates runtime-only role-isolation policies,
denies permanent schema/database creation, and keeps direct trigger-function
execution unavailable. PostgreSQL may still expose isolated temporary-object
creation through the database's `PUBLIC TEMP` default; this is not an
application grant and removing it would be a database-wide policy decision.
Role creation and passwords are intentionally not committed to migrations.

For the current live Supabase project, the automated migration-010
initialisation and dedicated runtime-role verification completed before the
client-identity API revision merged. Those workflow results do not prove the
current API release's authenticated role behaviour, the manual SQL catalogue
checks, Security Advisor clearance, browser acceptance, or field acceptance.
Those remain explicit release gates.

For a new Supabase project, a replacement runtime role, or policy recovery:

1. In Supabase, confirm the custom session-pooler username format for
   `funhouse_runtime` in **Connect**. Provision the PostgreSQL role and a distinct
   random password out of band; do not reuse the project owner password.
2. Add `SUPABASE_RUNTIME_DB_USER` and `SUPABASE_RUNTIME_DB_PASSWORD` to the
   protected GitHub `production` environment. Keep existing owner/migrator
   secrets unchanged for rollback.
3. Run **Initialize Live Supabase Database** from `main` using the owner or
   migrator identity. The migration chain atomically installs the runtime grants
   and policies before committing.
4. Run **Verify Live Supabase Runtime Role** from `main`. It must confirm the
   exact effective grants, policies, role attributes, and ownership separation
   through the same session pooler Vercel will use.
5. In Vercel, set `DB_USER` to the verified custom-role pooler username and
   `DB_PASSWORD` to its distinct password, then redeploy. Do not change the
   owner/migrator workflow secrets.
6. Confirm an authenticated FastAPI login and representative founder, manager,
   and facilitator DB-backed read/write operations. `/health` alone is
   insufficient because it does not query PostgreSQL.
7. In the Supabase SQL editor, verify all 14 rows below show
   `rls_enabled = true`, `rls_forced = false`, and an owner other than
   `funhouse_runtime`:

   ```sql
   WITH expected(name) AS (
     VALUES ('locations'), ('schools'), ('users'), ('guardians'), ('players'),
            ('consents'), ('products'), ('entitlements'), ('sessions'),
            ('attendance'), ('payments'), ('lessons'), ('student_metrics'),
            ('sync_log')
   )
   SELECT e.name,
          c.relrowsecurity AS rls_enabled,
          c.relforcerowsecurity AS rls_forced,
          pg_get_userbyid(c.relowner) AS owner
   FROM expected AS e
   LEFT JOIN pg_class AS c
     ON c.relnamespace = 'public'::regnamespace
    AND c.relname = e.name
    AND c.relkind IN ('r', 'p')
   ORDER BY e.name;
   ```

8. Confirm the only policies on those tables target `funhouse_runtime`, then
   verify the trigger function has a fixed empty path:

   ```sql
   SELECT tablename, policyname, cmd, roles
   FROM pg_policies
   WHERE schemaname = 'public'
     AND tablename IN (
       'locations', 'schools', 'users', 'guardians', 'players', 'consents',
       'products', 'entitlements', 'sessions', 'attendance', 'payments',
       'lessons', 'student_metrics', 'sync_log'
     )
   ORDER BY tablename, policyname;

   SELECT p.proname, p.proconfig
   FROM pg_proc AS p
   JOIN pg_namespace AS n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
     AND p.proname = 'funhouse_reject_consents_mutation'
     AND pg_get_function_identity_arguments(p.oid) = '';
   ```

   Expected: 24 `funhouse_runtime_*` policy rows whose `roles` value contains
   only `funhouse_runtime`, plus one function row whose `proconfig` contains
   `search_path=""`.
9. Rerun Supabase Security Advisor. It must remain at zero errors and zero
   warnings after the runtime-role policy installation.

### Runtime-role rollback and policy recovery

- **Before Vercel cutover:** if migration or runtime verification fails, stop.
  The transaction rolls back and the existing owner-backed API remains live.
  Correct role attributes, memberships, or grants before rerunning; do not
  change Vercel secrets.
- **After Vercel cutover:** if any DB-backed operation fails, first restore the
  previous owner-backed `DB_USER` and `DB_PASSWORD` in Vercel and redeploy.
  Verify login plus a representative write before changing database policies.
- **Policy-loss recovery:** run **Initialize Live Supabase Database** with the
  protected owner/migrator identity; its migration step forces runtime-role
  activation and atomically rebuilds the 24 policies. Then rerun **Verify Live
  Supabase Runtime Role** before attempting the Vercel runtime cutover again.
- Keep the old owner credential valid and migration-only through a soak period.
  Remove it from Vercel only after founder, manager, and facilitator flows pass;
  rotate or retire credentials last, never as the first rollback action.

**Tear down the one-off immediately** after the migration completes (terminate
the task/instance). It leaves no standing service. *(Not recommended
alternative: temporarily flipping RDS to publicly accessible — weakens the
private posture; do not use.)*

## 6. Build and publish the PWA to S3 + CloudFront  **[FOUNDER-RUN / LIVE CLOUD]** — Req 9.2

Build the PWA against the App Runner HTTPS URL, sync to the private bucket, then
invalidate CloudFront:

```bash
# from web/
VITE_API_BASE_URL="https://<apprunner_url>" npm run build     # produces web/dist/
aws s3 sync dist/ "s3://<web_bucket_name>/" --delete
aws cloudfront create-invalidation \
  --distribution-id <cloudfront_distribution_id> \
  --paths "/index.html" "/sw.js"
```

`index.html` and `sw.js` are served no-cache (see the `web` module) so the PWA
update propagates; content-hashed assets are long-cached.

## 7. Wire CORS (second apply)  **[FOUNDER-RUN / LIVE CLOUD]** — Req 7.3

The PWA origin (CloudFront) differs from the API origin (App Runner), so set the
API's allowlist to the CloudFront origin. This is a cheap App Runner config
update — no image rebuild:

```bash
# Use the cloudfront_origin output (https://<dist>.cloudfront.net).
terraform apply -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars \
  -var 'cors_origins=https://<cloudfront_domain>'
```

(Or set `cors_origins` in `locations/loc1.tfvars` and re-apply.) App Runner
redeploys with `FUNHOUSE_CORS_ORIGINS` set.

## 8. Verify — run the Smoke-Test Checklist  **[FOUNDER-RUN / LIVE CLOUD]** — Req 10

Follow `docs/smoke-test-checklist.md` end to end: load PWA over HTTPS → login →
offline capture → sync → read-back → CORS. All six steps must pass.

## 9. Offline verification gates (no AWS account needed)

These run against a saved plan and can gate the apply in CI:

```bash
terraform fmt -check -recursive
terraform init -backend=false && terraform validate
terraform show -json loc1.tfplan > loc1.plan.json
scripts/assert_residency.sh    loc1.plan.json     # V1: all Data_At_Rest af-south-1
scripts/assert_no_forbidden.sh loc1.plan.json     # V3: no LB/EKS/ECS/CW alarms
```

**Plan idempotency (V2, Req 8.2):** after Step 3/7, a second `terraform plan`
must report **"No changes"**:

```bash
terraform plan -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars
# expect: No changes. Your infrastructure matches the configuration.
```

## 10. Teardown  **[FOUNDER-RUN / LIVE CLOUD]** — Req 9.4

```bash
# Empty the S3 bucket first (CloudFront/S3 will not delete a non-empty bucket).
aws s3 rm "s3://<web_bucket_name>/" --recursive

# RDS has deletion_protection = true (see modules/database/main.tf). Set it to
# false there (and re-apply) or disable it on the instance in the console before
# destroy, otherwise terraform destroy will refuse to delete the database.

terraform destroy -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars
```

Then confirm nothing lingers:

- SSM parameters removed (`aws ssm get-parameters-by-path --path /funhouse/loc1`
  returns empty). Delete any manually-seeded secret values not managed by
  Terraform.
- The App Runner service, RDS instance (final snapshot taken unless skipped),
  CloudFront distribution, and S3 bucket are gone.
- The one-off migration context (Step 5) was already torn down.

## Operational notes

- **Failed migration:** `run_migrations` commits only on success; a failure
  leaves no partial schema. Fix connectivity/creds and re-run (idempotent).
- **App Runner deploy failure:** the previous healthy version keeps serving
  (default rollback); inspect CloudWatch service logs, fix, redeploy.
- **RDS unavailable:** `/health` stays green (no DB access); DB-backed endpoints
  return 5xx until RDS returns. 7-day PITR covers data-loss recovery.
- **Stale PWA after redeploy:** re-sync `dist/` and invalidate `/index.html` +
  `/sw.js` (Step 6).
- **State conflict:** S3-native lockfile prevents concurrent applies; resume a
  partial apply by re-running `apply` (idempotent).
