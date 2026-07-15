# -----------------------------------------------------------------------------
# Root module wiring (Design Deliverable (a) main.tf)
# -----------------------------------------------------------------------------
# Dependency order: network -> database -> secrets -> api, plus web (independent
# of the VPC). The graph provisions every Section 3.1 component: RDS, App
# Runner, S3 + CloudFront, and SSM (Req 8.3). No load balancer, EKS, ECS, or
# extra managed service is declared (Req 12).

module "network" {
  source = "./modules/network"

  location_slug = var.location_slug
  vpc_cidr      = var.vpc_cidr
}

module "database" {
  source = "./modules/database"

  location_slug         = var.location_slug
  subnet_ids            = module.network.private_subnet_ids
  security_group_id     = module.network.rds_security_group_id
  instance_class        = var.db_instance_class
  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  engine_version        = var.db_engine_version
  db_name               = var.db_name
  master_username       = var.db_master_username
  master_password       = var.db_master_password
}

module "secrets" {
  source = "./modules/secrets"

  ssm_prefix  = var.ssm_prefix
  db_password = var.db_master_password
  db_user     = var.db_master_username
  jwt_secret  = var.jwt_secret
  db_host     = module.database.address
  db_name     = module.database.db_name
}

module "api" {
  source = "./modules/api"

  location_slug       = var.location_slug
  region              = var.region
  ecr_repository_name = var.ecr_repository_name
  image_tag           = var.api_image_tag
  container_port      = var.container_port
  vpc_connector_arn   = module.network.vpc_connector_arn
  min_instances       = var.apprunner_min_instances
  max_instances       = var.apprunner_max_instances
  cpu                 = var.apprunner_cpu
  memory              = var.apprunner_memory

  jwt_secret_arn        = module.secrets.jwt_secret_arn
  db_password_arn       = module.secrets.db_password_arn
  db_user_arn           = module.secrets.db_user_arn
  db_host_arn           = module.secrets.db_host_arn
  db_name_arn           = module.secrets.db_name_arn
  secure_parameter_arns = module.secrets.all_parameter_arns

  # Set on the second apply once the CloudFront origin is known (Req 7.3).
  cors_origins = var.cors_origins

  # Flag-gated auto-migrate/seed on container start (Spec 3.5). Default false;
  # the deploy script sets these true on the first apply for a hands-off
  # bring-up (replacing the manual in-VPC one-off).
  run_migrations_on_start = var.run_migrations_on_start
  run_seed_on_start       = var.run_seed_on_start
}

module "web" {
  source = "./modules/web"

  location_slug = var.location_slug
  region        = var.region
}
