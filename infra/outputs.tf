# -----------------------------------------------------------------------------
# Root outputs (Design Deliverable (a) outputs.tf)
# -----------------------------------------------------------------------------
# Consumed by the runbook: the PWA build target (apprunner_url), CORS wiring
# (cloudfront_domain), and the migration DSN (rds_endpoint) (Req 8.3, 9.2).

output "apprunner_url" {
  description = "App Runner default HTTPS host for the Container_API."
  value       = "https://${module.api.service_url}"
}

output "cloudfront_domain" {
  description = "CloudFront domain serving the Revenue_PWA over HTTPS."
  value       = module.web.cloudfront_domain
}

output "cloudfront_origin" {
  description = "The https origin to add to the API FUNHOUSE_CORS_ORIGINS (Req 7.3)."
  value       = module.web.cloudfront_origin
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (for invalidations during redeploys)."
  value       = module.web.cloudfront_distribution_id
}

output "web_bucket_name" {
  description = "Private S3 bucket the PWA build is synced to."
  value       = module.web.bucket_name
}

output "rds_endpoint" {
  description = "RDS endpoint (host:port) for the migration one-off and app config."
  value       = module.database.endpoint
}

output "ssm_parameter_arns" {
  description = "All SSM parameter ARNs provisioned for this location."
  value       = module.secrets.all_parameter_arns
}
