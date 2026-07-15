variable "location_slug" {
  description = "Location identifier used to name API resources."
  type        = string
}

variable "region" {
  description = "AWS region (af-south-1)."
  type        = string
}

variable "ecr_repository_name" {
  description = "Name of the private ECR repo holding the Container_API image."
  type        = string
}

variable "image_tag" {
  description = "ECR image tag to deploy."
  type        = string
}

variable "container_port" {
  description = "Port the Container_API listens on."
  type        = number
}

variable "vpc_connector_arn" {
  description = "ARN of the App Runner VPC connector for private RDS egress (Design C1)."
  type        = string
}

variable "min_instances" {
  description = "App Runner autoscaling minimum instances."
  type        = number
}

variable "max_instances" {
  description = "App Runner autoscaling maximum instances."
  type        = number
}

variable "cpu" {
  description = "App Runner instance vCPU units."
  type        = string
}

variable "memory" {
  description = "App Runner instance memory (MB)."
  type        = string
}

# SSM SecureString / String ARNs (injected as runtime secrets — Req 5.3).
variable "jwt_secret_arn" {
  type        = string
  description = "SSM ARN for the JWT secret."
}

variable "db_password_arn" {
  type        = string
  description = "SSM ARN for the DB password."
}

variable "db_user_arn" {
  type        = string
  description = "SSM ARN for the DB user."
}

variable "db_host_arn" {
  type        = string
  description = "SSM ARN for the DB host."
}

variable "db_name_arn" {
  type        = string
  description = "SSM ARN for the DB name."
}

variable "secure_parameter_arns" {
  description = "All SSM parameter ARNs the instance role may read."
  type        = list(string)
}

variable "cors_origins" {
  description = "Value for FUNHOUSE_CORS_ORIGINS — the CloudFront PWA origin (Req 7.3). Set/updated after CloudFront exists."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Auto-migrate / seed on container start (Spec 3.5 one-command deploy).
# -----------------------------------------------------------------------------
# When true, App Runner sets RUN_MIGRATIONS_ON_START / RUN_SEED_ON_START so the
# container entrypoint applies the idempotent schema migration (and optionally
# the reference-data seed) before the server starts. Default false keeps the
# original manual in-VPC one-off path unchanged; the deploy script flips these
# to true for a hands-off first bring-up.
variable "run_migrations_on_start" {
  description = "Set RUN_MIGRATIONS_ON_START on the container so it runs idempotent migrations at startup."
  type        = bool
  default     = false
}

variable "run_seed_on_start" {
  description = "Set RUN_SEED_ON_START on the container so it runs the idempotent reference-data seed at startup."
  type        = bool
  default     = false
}
