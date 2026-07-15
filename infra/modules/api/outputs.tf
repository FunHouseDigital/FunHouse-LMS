output "service_url" {
  description = "App Runner default HTTPS URL (host only)."
  value       = aws_apprunner_service.this.service_url
}

output "service_arn" {
  description = "App Runner service ARN."
  value       = aws_apprunner_service.this.arn
}

output "access_role_arn" {
  description = "App Runner ECR access role ARN."
  value       = aws_iam_role.access.arn
}

output "instance_role_arn" {
  description = "App Runner instance role ARN."
  value       = aws_iam_role.instance.arn
}
