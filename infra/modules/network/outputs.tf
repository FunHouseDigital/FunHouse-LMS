output "vpc_id" {
  description = "The VPC id."
  value       = aws_vpc.this.id
}

output "private_subnet_ids" {
  description = "IDs of the two private subnets (for the DB subnet group)."
  value       = aws_subnet.private[*].id
}

output "rds_security_group_id" {
  description = "Security group id for the RDS instance."
  value       = aws_security_group.rds.id
}

output "connector_security_group_id" {
  description = "Security group id used by the App Runner VPC connector."
  value       = aws_security_group.connector.id
}

output "vpc_connector_arn" {
  description = "ARN of the App Runner VPC connector."
  value       = aws_apprunner_vpc_connector.this.arn
}
