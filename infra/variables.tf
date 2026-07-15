# -----------------------------------------------------------------------------
# Shared input variables (Design Data Models -> Per-location variables)
# -----------------------------------------------------------------------------
# Every per-location difference is expressed here so that standing up Location 2
# is a second `terraform apply -var-file=locations/loc2.tfvars` with no
# hand-authored, undocumented steps (Req 8.4).

variable "region" {
  description = "AWS region for all Data_At_Rest. MUST be af-south-1 (Req 1)."
  type        = string
  default     = "af-south-1"

  validation {
    condition     = var.region == "af-south-1"
    error_message = "POPIA residency: region must be af-south-1 (Req 1.1)."
  }
}

variable "location_slug" {
  description = "Short identifier for this FunHouse location (e.g. loc1, loc2). Used to namespace SSM parameters and name resources."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the private VPC (Design C1). Distinct per location."
  type        = string
  default     = "10.20.0.0/16"
}

variable "db_instance_class" {
  description = "RDS instance class. Smallest class supporting Location 1 scale (Req 2.2, Design D3)."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS gp3 allocated storage in GiB (Design C2)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "RDS storage autoscaling cap in GiB (Design C2)."
  type        = number
  default     = 50
}

variable "db_engine_version" {
  description = "PostgreSQL major/minor version (Req 2.1)."
  type        = string
  default     = "16.4"
}

variable "db_name" {
  description = "Initial database name created on the RDS instance."
  type        = string
  default     = "funhouse"
}

variable "apprunner_min_instances" {
  description = "App Runner autoscaling minimum instances. min=1 keeps a warm baseline (Design C3, Req 11 cost lever)."
  type        = number
  default     = 1
}

variable "apprunner_max_instances" {
  description = "App Runner autoscaling maximum instances (kept small for cost)."
  type        = number
  default     = 2
}

variable "apprunner_cpu" {
  description = "App Runner instance vCPU units (e.g. 256 = 0.25 vCPU)."
  type        = string
  default     = "256"
}

variable "apprunner_memory" {
  description = "App Runner instance memory in MB (e.g. 512 = 0.5 GB)."
  type        = string
  default     = "512"
}

variable "ssm_prefix" {
  description = "SSM Parameter Store namespace prefix for this location, e.g. /funhouse/loc1 (Design C4)."
  type        = string
}

variable "ecr_repository_name" {
  description = "Name of the private ECR repo holding the Container_API image (Design C3)."
  type        = string
  default     = "funhouse-api"
}

variable "container_port" {
  description = "Port the Container_API listens on inside the container."
  type        = number
  default     = 8000
}

variable "api_image_tag" {
  description = "ECR image tag for the Container_API to deploy on App Runner."
  type        = string
  default     = "latest"
}


# -----------------------------------------------------------------------------
# Secret values (Req 5.2, 5.4)
# -----------------------------------------------------------------------------
# These are supplied at apply time from an UNCOMMITTED source (e.g. a
# .gitignore'd *.secrets.tfvars or -var on the CLI) and are marked sensitive so
# Terraform never prints them. They are NEVER given defaults and NEVER committed.
variable "db_master_username" {
  description = "RDS master username (secret). Supply via uncommitted -var/-var-file."
  type        = string
  sensitive   = true
}

variable "db_master_password" {
  description = "RDS master password (secret). Supply via uncommitted -var/-var-file."
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "API JWT signing secret. Supply via uncommitted -var/-var-file."
  type        = string
  sensitive   = true
}

variable "cors_origins" {
  description = "FUNHOUSE_CORS_ORIGINS value (the CloudFront origin). Set on the second apply once CloudFront exists (Req 7.3)."
  type        = string
  default     = ""
}
