# -----------------------------------------------------------------------------
# Terraform remote state backend (Design D6)
# -----------------------------------------------------------------------------
# State is kept in a versioned, encrypted S3 bucket in af-south-1 so that a
# second operator can re-apply safely and so that state metadata never leaves
# the POPIA residency region (Req 1.1, 8.1).
#
# Locking uses the S3-native lockfile feature (`use_lockfile = true`), which
# requires Terraform >= 1.10. This intentionally avoids a DynamoDB lock table:
# DynamoDB would be an *additional managed service* outside PRD Section 3.1
# (Req 12.2). An S3 bucket is already a Section 3.1 primitive.
#
# The state bucket itself is a bootstrap resource: create it once (versioned +
# encrypted + Block Public Access) before the first `terraform init`. See
# infra/README.md for the one-time bootstrap commands.
#
# -----------------------------------------------------------------------------
# LOCAL-STATE FALLBACK (first MVP run)
# -----------------------------------------------------------------------------
# For a very first MVP apply, before the state bucket exists, you may comment
# out the entire `backend "s3"` block below. Terraform then defaults to local
# state (`terraform.tfstate` on disk). Migrate to the S3 backend afterwards
# with `terraform init -migrate-state`. Local state is NOT recommended beyond
# the first bootstrap because it is not shareable between operators.
# -----------------------------------------------------------------------------

terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket = "funhouse-tfstate-af-south-1"
    key    = "deployment/terraform.tfstate"
    region = "af-south-1"

    encrypt = true
    # S3-native state locking (Terraform >= 1.10) — no DynamoDB table required.
    use_lockfile = true
  }
}
