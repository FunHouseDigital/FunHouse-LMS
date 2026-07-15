# -----------------------------------------------------------------------------
# C3. API — ECR reference, IAM roles, App Runner service (Design C3)
# -----------------------------------------------------------------------------
# App Runner runs the existing Container_API image from a private ECR repo over
# HTTPS only, health-checks /health, egresses through the VPC connector to reach
# private RDS, min instances = 1, and injects config as plain env vars plus SSM
# SecureString references (Req 4, 5.3, 7).

data "aws_caller_identity" "current" {}

# Reference the existing private ECR repository (created/pushed out-of-band per
# the runbook). Using a data source keeps image publishing a runbook step.
data "aws_ecr_repository" "api" {
  name = var.ecr_repository_name
}

# -----------------------------------------------------------------------------
# IAM: access role (ECR pull) — assumed by the App Runner build service.
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "access_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "access" {
  name               = "funhouse-${var.location_slug}-apprunner-access"
  assume_role_policy = data.aws_iam_policy_document.access_assume.json
}

resource "aws_iam_role_policy_attachment" "access_ecr" {
  role       = aws_iam_role.access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# -----------------------------------------------------------------------------
# IAM: instance role — assumed by the running task; may read the SSM params and
# decrypt with the AWS-managed aws/ssm key. Least privilege: only
# ssm:GetParameters (+ GetParameter) on the specific ARNs and kms:Decrypt.
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "instance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "funhouse-${var.location_slug}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.instance_assume.json
}

# The AWS-managed aws/ssm key ARN, for the kms:Decrypt grant.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

data "aws_iam_policy_document" "instance_ssm" {
  statement {
    sid = "ReadSsmParameters"
    actions = [
      "ssm:GetParameters",
      "ssm:GetParameter",
    ]
    resources = var.secure_parameter_arns
  }

  statement {
    sid       = "DecryptSsmSecureStrings"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }
}

resource "aws_iam_role_policy" "instance_ssm" {
  name   = "funhouse-${var.location_slug}-ssm-read"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance_ssm.json
}

# -----------------------------------------------------------------------------
# App Runner service
# -----------------------------------------------------------------------------
resource "aws_apprunner_service" "this" {
  service_name                   = "funhouse-${var.location_slug}-api"
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.this.arn

  source_configuration {
    # HTTPS-only default *.awsapprunner.com domain is provided by App Runner;
    # there is no plaintext listener (Req 4.2, 4.3).
    authentication_configuration {
      access_role_arn = aws_iam_role.access.arn
    }
    auto_deployments_enabled = false

    image_repository {
      image_identifier      = "${data.aws_ecr_repository.api.repository_url}:${var.image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port = tostring(var.container_port)

        # Plain (non-secret) environment variables.
        runtime_environment_variables = {
          DB_PORT               = "5432"
          DB_SSLMODE            = "require" # TLS to RDS (Req 4.6, 7.2)
          TLS_REQUIRED          = "true"    # API rejects non-HTTPS (Req 4.3)
          AWS_REGION            = var.region
          FUNHOUSE_CORS_ORIGINS = var.cors_origins
        }

        # Secret / SSM-sourced values injected by ARN reference (Req 5.3).
        runtime_environment_secrets = {
          JWT_SECRET  = var.jwt_secret_arn
          DB_PASSWORD = var.db_password_arn
          DB_USER     = var.db_user_arn
          DB_HOST     = var.db_host_arn
          DB_NAME     = var.db_name_arn
        }
      }
    }
  }

  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = aws_iam_role.instance.arn
  }

  # Liveness health check on the public, DB-free /health path (Design C3).
  health_check_configuration {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  # Egress through the VPC connector so the service reaches private RDS.
  network_configuration {
    egress_configuration {
      egress_type       = "VPC"
      vpc_connector_arn = var.vpc_connector_arn
    }
    ingress_configuration {
      is_publicly_accessible = true
    }
  }

  tags = {
    Name = "funhouse-${var.location_slug}-api"
  }
}

resource "aws_apprunner_auto_scaling_configuration_version" "this" {
  auto_scaling_configuration_name = "fh-${var.location_slug}-asc"
  min_size                        = var.min_instances
  max_size                        = var.max_instances

  tags = {
    Name = "funhouse-${var.location_slug}-asc"
  }
}
