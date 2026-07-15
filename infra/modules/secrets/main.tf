# -----------------------------------------------------------------------------
# C4. Secrets — SSM Parameter Store (Design C4, D2)
# -----------------------------------------------------------------------------
# SecureString parameters (db password, db user, jwt secret) encrypted with the
# AWS-managed aws/ssm key (LOCKED decision — no CMK), standard tier (free), in
# af-south-1. Plain String parameters for non-secret convenience values.
#
# The module exposes parameter ARNs (referenced by the API instance role and
# App Runner service), never the literal secret values (Req 5.4). The values
# themselves are supplied via -var / -var-file at apply from an uncommitted
# source (Req 5.2).
#
# key_id = "alias/aws/ssm" selects the AWS-managed SSM key explicitly.

locals {
  key_id = "alias/aws/ssm"
}

resource "aws_ssm_parameter" "db_password" {
  name   = "${var.ssm_prefix}/db/password"
  type   = "SecureString"
  key_id = local.key_id
  value  = var.db_password

  tags = { Secret = "true" }
}

resource "aws_ssm_parameter" "db_user" {
  name   = "${var.ssm_prefix}/db/user"
  type   = "SecureString"
  key_id = local.key_id
  value  = var.db_user

  tags = { Secret = "true" }
}

resource "aws_ssm_parameter" "jwt_secret" {
  name   = "${var.ssm_prefix}/jwt/secret"
  type   = "SecureString"
  key_id = local.key_id
  value  = var.jwt_secret

  tags = { Secret = "true" }
}

# Non-secret convenience values (plain String).
resource "aws_ssm_parameter" "db_host" {
  name  = "${var.ssm_prefix}/db/host"
  type  = "String"
  value = var.db_host
}

resource "aws_ssm_parameter" "db_name" {
  name  = "${var.ssm_prefix}/db/name"
  type  = "String"
  value = var.db_name
}
