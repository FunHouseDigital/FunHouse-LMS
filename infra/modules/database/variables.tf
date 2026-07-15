variable "location_slug" {
  description = "Location identifier used to name DB resources."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet ids for the DB subnet group."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group id for the RDS instance (ingress only from connector)."
  type        = string
}

variable "instance_class" {
  description = "RDS instance class (Req 2.2)."
  type        = string
}

variable "allocated_storage" {
  description = "gp3 allocated storage in GiB."
  type        = number
}

variable "max_allocated_storage" {
  description = "Storage autoscaling cap in GiB."
  type        = number
}

variable "engine_version" {
  description = "PostgreSQL engine version."
  type        = string
}

variable "db_name" {
  description = "Initial database name."
  type        = string
}

variable "master_username" {
  description = "RDS master username. Supplied from SSM-sourced value at apply — never an inline literal (Req 5.2, 5.4)."
  type        = string
  sensitive   = true
}

variable "master_password" {
  description = "RDS master password. Supplied from SSM-sourced value at apply — never an inline literal (Req 5.2, 5.4)."
  type        = string
  sensitive   = true
}
