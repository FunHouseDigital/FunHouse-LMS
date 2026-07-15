# Zero-local-tooling deploy (from GitHub)

Deploy the entire FunHouse stack to AWS **af-south-1** from your browser — no
AWS CLI, Terraform, Docker, or Node.js on your machine. Everything runs on a
GitHub Actions runner via the **Deploy** workflow
(`.github/workflows/deploy.yml`).

## One-time setup — create 4 repository secrets

In GitHub: **Settings → Secrets and variables → Actions → New repository
secret**. Add these four:

| Secret | What it is |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | Access key of an IAM user with deploy permissions. |
| `AWS_SECRET_ACCESS_KEY` | That IAM user's secret access key. |
| `DB_MASTER_PASSWORD` | Strong random RDS master password — generate once, keep it. |
| `JWT_SECRET` | Strong random API JWT signing secret — generate once, keep it. |

Notes:

- **`AWS_*`** come from an IAM user that can create the stack. For the MVP,
  `AdministratorAccess` is the simplest; tighten to least-privilege (or GitHub
  OIDC federation) later.
- **`DB_MASTER_PASSWORD`** and **`JWT_SECRET`** are strong random values you
  generate **once** and keep. They must **persist across runs** so Terraform
  state stays consistent — the workflow deliberately does **not** generate
  secrets in CI. For example:

  ```bash
  openssl rand -base64 24   # DB_MASTER_PASSWORD
  openssl rand -base64 48   # JWT_SECRET
  ```

- **Optional:** `DB_MASTER_USERNAME`. If you don't set it, the workflow defaults
  to `funhouse_admin`.

## Deploy

1. Go to the **Actions** tab → **Deploy** workflow.
2. Click **Run workflow**.
3. Optionally set inputs (defaults are fine for Location 1):
   - `location` — the location slug (default `loc1`, matches
     `infra/locations/<slug>.tfvars`).
   - `run_seed` — seed reference data on container start (default `true`).
4. Click the green **Run workflow** button.

When the run finishes, the **live CloudFront URL** (the PWA — open this) and the
**App Runner API URL** are shown on the run's summary page.

## Notes

- **Re-runnable.** The workflow is idempotent — run it again any time to
  redeploy or update the stack.
- **DB migrates/seeds itself.** On first boot the container applies the
  idempotent schema migrations and (if `run_seed` is true) the reference-data
  seed, so the runner never needs to reach the private RDS.
- **No secrets or state are committed.** Secrets are injected from the GitHub
  repo secrets at run time; Terraform state lives in the encrypted
  `funhouse-tfstate-af-south-1` S3 bucket (created automatically if missing).
- After a successful deploy, verify with `docs/smoke-test-checklist.md`.
