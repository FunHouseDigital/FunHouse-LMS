# -----------------------------------------------------------------------------
# C1. Networking — minimal private VPC (Design C1, D1)
# -----------------------------------------------------------------------------
# A private VPC with exactly two private subnets (the minimum a DB subnet group
# requires) and NO Internet Gateway, NO NAT gateway, and NO public subnet.
# Nothing in the VPC needs outbound internet: the API's only egress dependency
# is RDS, reached through the App Runner VPC connector (Req 4.5, 7.2, 12.1,
# 12.2). Route tables are local-only.

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "funhouse-${var.location_slug}-vpc"
  }
}

# Two private subnets in two AZs — two AZs only to satisfy the RDS DB subnet
# group requirement; the DB instance itself is Single-AZ (Design D3).
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  # Explicitly private: never auto-assign public IPs.
  map_public_ip_on_launch = false

  tags = {
    Name = "funhouse-${var.location_slug}-private-${count.index + 1}"
    Tier = "private"
  }
}

# Local-only route table (no 0.0.0.0/0 route -> no IGW, no NAT).
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "funhouse-${var.location_slug}-private-rt"
  }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# -----------------------------------------------------------------------------
# Security groups
# -----------------------------------------------------------------------------
# The App Runner VPC connector's ENIs live in this SG; egress to RDS:5432 only.
resource "aws_security_group" "connector" {
  name        = "funhouse-${var.location_slug}-apprunner-connector"
  description = "App Runner VPC connector ENIs; egress to RDS only."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "funhouse-${var.location_slug}-sg-apprunner-connector"
  }
}

# RDS SG: ingress TCP 5432 ONLY from the connector SG; no public ingress.
resource "aws_security_group" "rds" {
  name        = "funhouse-${var.location_slug}-rds"
  description = "RDS PostgreSQL; ingress 5432 only from the App Runner connector."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "funhouse-${var.location_slug}-sg-rds"
  }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_connector" {
  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL from App Runner VPC connector only."
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.connector.id
}

resource "aws_vpc_security_group_egress_rule" "connector_to_rds" {
  security_group_id            = aws_security_group.connector.id
  description                  = "Egress to RDS PostgreSQL only."
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.rds.id
}

# -----------------------------------------------------------------------------
# App Runner VPC connector (native App Runner feature — not a load balancer,
# not a new managed service; stays within Section 3.1). Attached to both
# private subnets and the connector SG so App Runner can reach private RDS.
# -----------------------------------------------------------------------------
resource "aws_apprunner_vpc_connector" "this" {
  vpc_connector_name = "funhouse-${var.location_slug}-connector"
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.connector.id]
}
