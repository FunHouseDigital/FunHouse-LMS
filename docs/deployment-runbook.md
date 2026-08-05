# FunHouse Deployment Runbook (Spec 3.5)

> **Zero-local-tooling deploy:** see [`docs/deploy-from-github.md`](deploy-from-github.md)
> to deploy the whole stack from the browser via the **Deploy** GitHub Actions
> workflow (Actions → Deploy → Run workflow) — no local AWS CLI, Terraform,
> Docker, or Node.js required.


The ordered, end-to-end procedure to **stand up, operate, and tear down** the
FunHouse production deployment on AWS **af-south-1**, strictly within PRD §3.1.
It is written so a **non-founder operator** can follow it without tribal
knowledge (Req 9.5).

> **Live-cloud steps are founder-run.** Steps that touch a real AWS account
> (marked **[FOUNDER-RUN / LIVE CLOUD]**) require live AWS credentials and are
> executed by the founder/operator, not committed automation. The reproducible
> artifacts (Terraform, this runbook, the smoke-test checklist, the assertion
> scripts, and the migration shim) live in the repo; this document is the
> procedure to run them.

## Automated deploy (recommended)

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
Applied migrations: 001_schema.sql, 002_consents_append_only.sql, 003_role_facilitator.sql, 004_users_school_id.sql, 005_public_schema_lockdown.sql, 006_consents_function_search_path.sql
  Created: locations, schools, users, players, guardians, consents, products, entitlements, sessions, attendance, payments, lessons, student_metrics, sync_log
  Already present: (none)
```

Re-running is a safe no-op (idempotent): a second run reports `Created: (none)`
and lists the tables under `Already present` (Req 3.2).

### Migrations 005-006 security contract and Supabase rollout

Migration `005_public_schema_lockdown.sql` makes the FunHouse schema unavailable
through Supabase's Data API. Every replay deliberately removes **all** RLS
policies from the 14 FunHouse tables, enables non-forced RLS, and removes direct
object privileges from `PUBLIC`, `anon`, `authenticated`, `service_role`, and
`authenticator`. Do not add a Supabase REST policy out of band: a later migration
run will remove it. Any future design that uses Supabase REST must explicitly
supersede migration 005 and introduce a reviewed policy model.

The migration requires every FunHouse table to be owned by the migration/runtime
role. This preserves the existing direct FastAPI psycopg path because table
owners bypass non-forced RLS. Before applying it to Supabase, confirm that the
Vercel `DB_USER` is the same owner-backed `postgres.<project-ref>` pooler identity
used by the **Initialize Live Supabase Database** workflow. Stop if Vercel uses a
different role; first separate the owner, migrator, and API roles with explicit
policies and grants.

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

For the live Supabase project after this change reaches `main`:

1. Run **Initialize Live Supabase Database** from `main`. It now always runs the
   idempotent migration chain before seed/bootstrap and its database-backed login
   probe.
2. Confirm an authenticated FastAPI login and at least one normal DB-backed
   read/write operation. `/health` alone is insufficient because it does not
   query PostgreSQL.
3. In the Supabase SQL editor, verify all 14 rows below show
   `rls_enabled = true` and `rls_forced = false`:

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

4. Confirm `pg_policies` has no rows for those tables. Then verify the trigger
   function has a fixed empty path:

   ```sql
   SELECT p.proname, p.proconfig
   FROM pg_proc AS p
   JOIN pg_namespace AS n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
     AND p.proname = 'funhouse_reject_consents_mutation'
     AND pg_get_function_identity_arguments(p.oid) = '';
   ```

   Expected: one row whose `proconfig` contains `search_path=""`.
5. Rerun Supabase Security Advisor. Both `rls_disabled_in_public` and
   `function_search_path_mutable` must be gone before considering the security
   incident resolved.

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
