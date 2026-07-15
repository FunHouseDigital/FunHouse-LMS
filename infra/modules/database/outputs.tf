output "endpoint" {
  description = "RDS connection endpoint (host:port)."
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "RDS hostname (without port)."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "RDS port."
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Initial database name."
  value       = aws_db_instance.this.db_name
}
