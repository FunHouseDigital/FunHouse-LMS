# -----------------------------------------------------------------------------
# Location 2 variable set (Req 8.4)
# -----------------------------------------------------------------------------
# Standing up a second location is a second apply with this var-file and no
# hand-authored, undocumented steps:
#
#   terraform apply -var-file=locations/loc2.tfvars -var-file=loc2.secrets.tfvars
#
# Only the location-distinguishing values differ (slug, VPC CIDR, SSM prefix).
# Use a separate state key/workspace per location so the two never collide.

region        = "af-south-1"
location_slug = "loc2"
vpc_cidr      = "10.30.0.0/16"
ssm_prefix    = "/funhouse/loc2"

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

# cors_origins set on the second apply to the Location 2 CloudFront origin.

# Auto-migrate/seed on container start (Spec 3.5). Default false; the deploy
# script overrides these to true via -var on its first apply.
run_migrations_on_start = false
run_seed_on_start       = false
