# -----------------------------------------------------------------------------
# Location 1 variable set (Req 8.4)
# -----------------------------------------------------------------------------
# NON-SECRET per-location configuration only. Secret values (db_master_username,
# db_master_password, jwt_secret) are supplied separately from an UNCOMMITTED
# source at apply time — see infra/README.md and docs/deployment-runbook.md.
#
#   terraform apply -var-file=locations/loc1.tfvars -var-file=loc1.secrets.tfvars

region        = "af-south-1"
location_slug = "loc1"
vpc_cidr      = "10.20.0.0/16"
ssm_prefix    = "/funhouse/loc1"

db_instance_class        = "db.t4g.micro"
db_allocated_storage     = 20
db_max_allocated_storage = 50
db_engine_version        = "16.4"
db_name                  = "funhouse"

apprunner_min_instances = 1
apprunner_max_instances = 2
apprunner_cpu           = "256"
apprunner_memory        = "512"

ecr_repository_name = "funhouse-api"
container_port      = 8000
api_image_tag       = "latest"

# cors_origins is left empty on the first apply and set to the CloudFront origin
# on the second apply (Req 7.3), e.g.:
# cors_origins = "https://dxxxxxxxxxxxxx.cloudfront.net"

# Auto-migrate/seed on container start (Spec 3.5). Default false here: the
# one-command deploy script (scripts/deploy.ps1) overrides these to true via
# -var on its first apply. Leaving them false preserves the manual in-VPC
# one-off migration path.
run_migrations_on_start = false
run_seed_on_start       = false
