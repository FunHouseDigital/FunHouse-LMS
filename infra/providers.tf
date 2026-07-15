# -----------------------------------------------------------------------------
# Provider configuration (Design Deliverable (a) providers.tf)
# -----------------------------------------------------------------------------
# The aws provider is pinned to af-south-1 so that every resource that does not
# explicitly override its region is created in the sole permitted residency
# region (Req 1.1, 1.2, 8.1). CloudFront is a global edge service but its
# S3 origin bucket (the only Data_At_Rest) stays in af-south-1 (Design D5).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "funhouse-os"
      Spec      = "3.5-deployment"
      Location  = var.location_slug
      ManagedBy = "terraform"
    }
  }
}
