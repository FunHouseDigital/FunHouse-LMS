# Parameter ARNs are exported so the API module references ARNs, never literals
# (Req 5.4).
output "db_password_arn" {
  description = "ARN of the db password SecureString parameter."
  value       = aws_ssm_parameter.db_password.arn
}

output "db_user_arn" {
  description = "ARN of the db user SecureString parameter."
  value       = aws_ssm_parameter.db_user.arn
}

output "jwt_secret_arn" {
  description = "ARN of the JWT secret SecureString parameter."
  value       = aws_ssm_parameter.jwt_secret.arn
}

output "db_host_arn" {
  description = "ARN of the db host String parameter."
  value       = aws_ssm_parameter.db_host.arn
}

output "db_name_arn" {
  description = "ARN of the db name String parameter."
  value       = aws_ssm_parameter.db_name.arn
}

output "secure_parameter_arns" {
  description = "All SecureString parameter ARNs (for the instance-role ssm:GetParameters grant)."
  value = [
    aws_ssm_parameter.db_password.arn,
    aws_ssm_parameter.db_user.arn,
    aws_ssm_parameter.jwt_secret.arn,
  ]
}

output "all_parameter_arns" {
  description = "All parameter ARNs (SecureString + String)."
  value = [
    aws_ssm_parameter.db_password.arn,
    aws_ssm_parameter.db_user.arn,
    aws_ssm_parameter.jwt_secret.arn,
    aws_ssm_parameter.db_host.arn,
    aws_ssm_parameter.db_name.arn,
  ]
}
