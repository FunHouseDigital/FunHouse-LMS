# FunHouse Deployment — Terraform IaC (`infra/`)

Reproducible infrastructure-as-code for the first production deployment of the
FunHouse Operating System to AWS **af-south-1**, strictly within PRD §3.1
(Portable Core). This tree provisions **only**: a private VPC + App Runner VPC
connector, RDS PostgreSQL, SSM Parameter Store, App Runner, and S3 + CloudFront.
No load balancer, EKS, ECS, or any extra managed service; **no monitoring beyond
CloudWatch defaults** (Req 12).

> This README is the operator-facing companion to `docs/deployment-runbook.md`
> (ordered end-to-end procedure) and `docs/smoke-test-checklist.md` (validation).
> Live `terraform apply`, ECR push, SSM secret seeding, and the end-to-end smoke
> run are **founder-run steps** requiring live AWS credentials.

## Prerequisites

- **Terraform ≥ 1.10** (S3-native state locking; no DynamoDB lock table).
- **AWS credentials** for an account/role permitted in af-south-1.
- **Docker** (to build + push the Container_API image to ECR).
- A built PWA bundle (`web/dist/`) and a built API image.

## Layout

```
infra/
  backend.tf     # S3 remote state (af-south-1, versioned, encrypted, S3 lockfile)
  providers.tf   # aws provider pinned region = af-south-1
  variables.tf   # shared + secret variable declarations
  main.tf        # module wiring: network -> database -> secrets -> api, + web
  outputs.tf     # apprunner_url, cloudfront_domain, rds_endpoint, ssm ARNs
  modules/
    network/     # C1: private VPC, 2 private subnets, SGs, App Runner VPC connector
    database/    # C2: RDS db.t4g.micro Single-AZ gp3 encrypted, 7-day backups, force_ssl
    secrets/     # C4: SSM SecureString + String params (AWS-managed aws/ssm key)
    api/         # C3: ECR ref, App Runner access/instance IAM roles, App Runner service
    web/         # C5: private S3 + CloudFront (OAC, redirect-to-HTTPS, SPA fallback)
  locations/
    loc1.tfvars  # Location 1 (non-secret) variable set
    loc2.tfvars  # Location 2 (non-secret) variable set — re-apply target
  scripts/
    assert_residency.sh     # V1: fail if any Data_At_Rest resource is not af-south-1
    assert_no_forbidden.sh  # V3: fail if plan contains LB/EKS/ECS/extra services/CW alarms
    fixtures/               # sample `terraform show -json` (passing + failing) for dry-runs
```

## Secrets are never committed (Req 5.2, 5.4)

The `loc*.tfvars` files hold **non-secret** per-location config only. The secret
values are supplied at apply time from an **uncommitted** file (matched by the
`*.secrets.tfvars` pattern in `.gitignore`) or via `-var` on the CLI:

```hcl
# loc1.secrets.tfvars  (DO NOT COMMIT — matched by .gitignore)
db_master_username = "funhouse_admin"
db_master_password = "…generated…"
jwt_secret         = "…generated…"
```

Terraform stores these into SSM SecureString parameters; App Runner reads them
by **ARN reference** at runtime. No secret literal appears anywhere in the repo.

## One-time state-bucket bootstrap (D6)

The S3 remote-state bucket in `backend.tf` must exist before the first
`terraform init`. Create it once (versioned + encrypted + Block Public Access):

```bash
aws s3api create-bucket --bucket funhouse-tfstate-af-south-1 \
  --region af-south-1 --create-bucket-configuration LocationConstraint=af-south-1
aws s3api put-bucket-versioning --bucket funhouse-tfstate-af-south-1 \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket funhouse-tfstate-af-south-1 \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket funhouse-tfstate-af-south-1 \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

> **Local-state fallback (first MVP run only):** comment out the `backend "s3"`
> block in `backend.tf` to use local state, then `terraform init -migrate-state`
> after the bucket exists. See the comment header in `backend.tf`.

## Init / plan / apply

```bash
terraform init                                   # S3 backend
terraform plan  -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars
terraform apply -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars
```

Capture the outputs (`apprunner_url`, `cloudfront_domain`, `cloudfront_origin`,
`rds_endpoint`, `web_bucket_name`, `cloudfront_distribution_id`) — the runbook
consumes them.

**Location 2** is the *same* command with a different var-file (Req 8.4) — no
hand-authored, undocumented steps. Use a distinct state key/workspace per
location so their states never collide:

```bash
terraform workspace new loc2      # or a separate backend key
terraform apply -var-file=locations/loc2.tfvars -var-file=loc2.secrets.tfvars
```

## Offline validation (no AWS account needed)

```bash
terraform fmt -check -recursive
terraform init -backend=false && terraform validate
```

## V2 — Plan idempotency (Req 8.2)

After a successful `apply`, a **second** `terraform plan` MUST report
**"No changes. Your infrastructure matches the configuration."** — i.e. the
Reproducible_Setup is a no-op on re-run, mirroring the idempotency of
`run_migrations`. To verify:

```bash
terraform apply -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars
terraform plan  -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars   # expect: No changes
```

> Note: on the second apply you additionally set `cors_origins` to the
> CloudFront origin (Req 7.3). That is an intended one-time config update; after
> it is applied, subsequent plans are again "No changes".

## Verification assertion scripts

Both scripts parse `terraform show -json <planfile>` and exit non-zero on a
violation. They require only `jq` (no AWS calls) and can be dry-run offline
against the committed fixtures:

```bash
scripts/assert_residency.sh    scripts/fixtures/plan_pass.json   # exit 0
scripts/assert_residency.sh    scripts/fixtures/plan_fail.json   # exit non-zero
scripts/assert_no_forbidden.sh scripts/fixtures/plan_pass.json   # exit 0
scripts/assert_no_forbidden.sh scripts/fixtures/plan_fail.json   # exit non-zero
```

- `assert_residency.sh` (**V1**, Req 1.1/1.2/5.5/6.3/13.1) — every Data_At_Rest
  resource (RDS instance, RDS backups, S3 buckets, SSM parameters, data-bearing
  log groups) must resolve to **af-south-1**.
- `assert_no_forbidden.sh` (**V3**, Req 12.1/12.2/12.3) — no load balancer
  (`aws_lb`/`aws_alb`/ELB), EKS (`aws_eks_*`), ECS (`aws_ecs_*`), any managed
  service outside the allowed set, or any CloudWatch alarm/dashboard.

---

## Cost Estimate — Location 1, af-south-1, monthly (Req 11)

Realistic af-south-1 estimates at Location-1 scale (single site, light traffic),
rounded conservatively upward to keep the ceiling honest.

| Component | Configuration | Est. USD/mo |
| --- | --- | --- |
| RDS PostgreSQL | db.t4g.micro, Single-AZ, on-demand | ~$16 |
| RDS storage | gp3, 20 GiB | ~$3 |
| RDS backups | ~20 GiB, near free allotment + small overage | ~$1 |
| App Runner | 1 instance (0.25 vCPU / 0.5 GB), min=1, light requests | ~$25–35 |
| S3 (static bundle) | <1 GiB storage + minimal requests | ~$0.50 |
| CloudFront | low traffic, few GB egress (largely free tier) | ~$1–3 |
| SSM Parameter Store | standard tier SecureString | **$0** |
| KMS | **AWS-managed keys** (aws/rds, aws/ssm) | **$0** |
| VPC connector | native App Runner egress | **$0** |
| NAT gateway | **not provisioned** (no outbound internet need) | **$0** |
| S3 Terraform state | tiny versioned bucket | ~$0.10 |
| CloudWatch | defaults only, low volume (mostly free tier) | ~$0–1 |
| **Total** | | **≈ $47–60 / mo** |

**Under the US$80 ceiling (Req 11.2).** Biggest lever: **App Runner `min=1`**
(warm baseline for predictable sync latency; scale-to-zero would cut cost but
add cold-start latency — a documented trade-off). If a future change pushes the
sum toward the ceiling, record it as an explicit open question (Req 11.3) rather
than absorb it silently.

> **Monitoring note (Req 12.3):** CloudWatch **defaults only** — App Runner
> service logs and RDS default metrics land automatically. No dashboards,
> alarms, custom log groups, X-Ray, or third-party monitoring are provisioned.

## Migration-Equivalence Table (Req 12.4, 12.5)

Every AWS component maps to a documented non-AWS equivalent so Location 2 or a
June 2027 migration is a **re-run, not a rewrite**.

| AWS component (this deployment) | Non-AWS equivalent | Migration action |
| --- | --- | --- |
| RDS for PostgreSQL | Managed Postgres (VPS provider) or self-hosted PostgreSQL | Point `DB_HOST`/`DB_*` at the new server, `DB_SSLMODE=require`, run `run_migrations` + `seed`. No code change. |
| App Runner | Any Docker host / VPS + reverse proxy (Nginx/Caddy) for TLS | `docker run` the same image; proxy terminates TLS; same env vars (secrets from host env/Vault). |
| S3 + CloudFront | Any object store + CDN, or an Nginx static host | `rsync web/dist/` to the host/bucket; front with CDN/Nginx; HTTPS via Let's Encrypt. |
| SSM Parameter Store | Host env vars / `.env` (uncommitted) or HashiCorp Vault | Recreate the same keys as env/Vault entries; app/proxy read at boot. |
| CloudWatch (defaults) | Host/container logs (`docker logs`, journald) | No app change; logs go to the host log system. |
| VPC connector + private subnets | Host-local networking / firewall rules | Firewall the DB to the app host; no managed connector needed. |
| KMS (AWS-managed keys) | Provider disk encryption / LUKS | Enable volume encryption on the host/DB. |
| ECR | Any container registry (Docker Hub, GHCR, self-hosted) | Push the same image; pull on the host. |

Because the Container_API depends only on a **standard libpq DSN via env** and
runs auth in-process, the whole migration is **env changes + a re-run of
`run_migrations`**.

## Bedrock Cross-Region Model-Inference Residency Rule (Req 13)

- **Rule (in force now):** **All Data_At_Rest remains in af-south-1** regardless
  of any future model-inference configuration (Req 13.1). This deployment
  provisions **no** Bedrock and **no** cross-region path.
- **Phase 2 is gated / out of scope:** the Lesson Engine (Amazon Bedrock) is not
  built here; this records the governing rule only (Req 13.4).
- **If cross-region inference is ever required**, it MUST be a **named,
  POPIA-justified documented decision** that (a) names the inference region,
  (b) states exactly which data transits the region boundary and why, and
  (c) explains why the arrangement remains POPIA-defensible (Req 13.2, 13.3).
- At time of writing Bedrock is not offered in af-south-1; should Phase 2
  proceed, the nearest appropriate Bedrock region (e.g. an EU region such as
  `eu-central-1`) is the **candidate to evaluate and name** in that future
  decision. Enabling it would also require the outbound-internet change noted in
  Design D1 (NAT gateway / VPC endpoints, costed then). **No infrastructure is
  built now.**

## Decisions locked into this IaC

- **Migration execution:** ephemeral in-VPC one-off runs `run_migrations`, then
  is torn down — no standing service, no public RDS exposure (Design C6, Open
  Question 1 recommended). See `docs/deployment-runbook.md`.
- **KMS:** AWS-managed keys (`aws/rds`, `aws/ssm`) — no dedicated CMKs (Design
  Open Question 2 recommended, $0). A CMK is a documented later upgrade if an
  audit requires explicit key policies/rotation.
