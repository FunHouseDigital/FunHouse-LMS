# -----------------------------------------------------------------------------
# C2. RDS PostgreSQL — private, encrypted, backed up (Design C2, D3)
# -----------------------------------------------------------------------------
# db.t4g.micro, Single-AZ, gp3, encrypted at rest with the AWS-managed aws/rds
# key (LOCKED decision — no dedicated CMK), 7-day automated backups, not
# publicly accessible, TLS enforced via rds.force_ssl (Req 1, 2, 4.6, 7.2).

# DB subnet group across the two private subnets (Design D1).
resource "aws_db_subnet_group" "this" {
  name       = "funhouse-${var.location_slug}-db-subnets"
  subnet_ids = var.subnet_ids

  tags = {
    Name = "funhouse-${var.location_slug}-db-subnets"
  }
}

# Parameter group enforcing TLS in transit (Req 4.6, 7.2).
resource "aws_db_parameter_group" "this" {
  name   = "funhouse-${var.location_slug}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# AWS-managed KMS key for RDS storage encryption (aws/rds). We look it up rather
# than create a CMK — LOCKED decision (Design Open Question 2 recommended: $0).
data "aws_kms_alias" "rds" {
  name = "alias/aws/rds"
}

resource "aws_db_instance" "this" {
  identifier     = "funhouse-${var.location_slug}"
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  # Single-AZ (Design D3, cost).
  multi_az = false

  # gp3 storage with autoscaling cap.
  storage_type          = "gp3"
  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage

  # Encryption at rest with the AWS-managed aws/rds key (no CMK — locked).
  storage_encrypted = true
  kms_key_id        = data.aws_kms_alias.rds.target_key_arn

  db_name  = var.db_name
  username = var.master_username
  password = var.master_password
  port     = 5432

  # Private posture (Design D1, Req 12).
  publicly_accessible    = false
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  parameter_group_name   = aws_db_parameter_group.this.name

  # Automated backups, 7-day retention, PITR (Req 2.3, 2.4, Design D3).
  backup_retention_period = 7
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot   = true

  # Operational hygiene for a small single-site deployment.
  auto_minor_version_upgrade = true
  deletion_protection        = true
  skip_final_snapshot        = false
  final_snapshot_identifier  = "funhouse-${var.location_slug}-final"
  apply_immediately          = false

  tags = {
    Name = "funhouse-${var.location_slug}-rds"
  }
}
