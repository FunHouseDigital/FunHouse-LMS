variable "location_slug" {
  description = "Location identifier used to name web hosting resources."
  type        = string
}

variable "region" {
  description = "AWS region for the S3 origin bucket (af-south-1, Req 6.3)."
  type        = string
}
