variable "ssm_prefix" {
  description = "SSM namespace prefix for this location, e.g. /funhouse/loc1 (Design C4)."
  type        = string
}

# Secret VALUES are supplied out-of-band (runbook / -var at apply) and are never
# committed (Req 5.2, 5.4). They are marked sensitive so Terraform never prints
# them. The *.tfvars files that carry them stay .gitignore'd.
variable "db_password" {
  description = "RDS master password (SecureString). Supplied at apply; never committed."
  type        = string
  sensitive   = true
}

variable "db_user" {
  description = "RDS master username (SecureString). Supplied at apply; never committed."
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "API JWT signing secret (SecureString). Supplied at apply; never committed."
  type        = string
  sensitive   = true
}

variable "db_host" {
  description = "RDS endpoint host (String, non-secret convenience)."
  type        = string
}

variable "db_name" {
  description = "Database name (String, non-secret)."
  type        = string
}
