# FunHouse Deployment Runbook (Spec 3.5)

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
Applied migrations: 001_schema.sql, 002_consents_append_only.sql, 003_role_facilitator.sql
  Created: locations, schools, users, players, guardians, consents, products, entitlements, sessions, attendance, payments, lessons, student_metrics, sync_log
  Already present: (none)
```

Re-running is a safe no-op (idempotent): a second run reports `Created: (none)`
and lists the tables under `Already present` (Req 3.2).

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
