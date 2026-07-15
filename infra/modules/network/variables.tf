variable "location_slug" {
  description = "Location identifier used to name networking resources."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
}
