#!/usr/bin/env bash
# One-command automated deploy for the FunHouse Operating System (Spec 3.5).
#
# POSIX-ish bash equivalent of scripts/deploy.ps1 (the PowerShell script is the
# primary artifact, since the founder is on Windows). Stands up / updates the
# whole stack on AWS af-south-1:
#
#   preflight -> tfstate bucket -> secrets -> ECR build/push ->
#   terraform apply (phase 1, migrate+seed on start) -> PWA build/publish ->
#   terraform apply (phase 2, CORS) -> done
#
# The container entrypoint runs the idempotent migration + seed itself on first
# boot (RUN_MIGRATIONS_ON_START / RUN_SEED_ON_START set here), so no manual
# in-VPC one-off is needed. Fail-fast and re-runnable; no secret or state is
# ever committed.
#
# Prerequisites: aws CLI v2, terraform >= 1.10, docker (running); node+npm for
# the PWA step (optional — skipped with manual instructions if absent). An
# authenticated AWS session (e.g. `aws sso login --profile funhouse`).
#
# Usage:
#   scripts/deploy.sh [-l loc1] [-r af-south-1] [-p funhouse]
#
# Next step after success: docs/smoke-test-checklist.md

set -euo pipefail

LOCATION="loc1"
REGION="af-south-1"
PROFILE="funhouse"
ECR_REPO="funhouse-api"
IMAGE_TAG="latest"

while getopts "l:r:p:t:h" opt; do
	case "$opt" in
	l) LOCATION="$OPTARG" ;;
	r) REGION="$OPTARG" ;;
	p) PROFILE="$OPTARG" ;;
	t) IMAGE_TAG="$OPTARG" ;;
	h)
		grep '^#' "$0" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	*)
		echo "Unknown option. Use -h for help." >&2
		exit 2
		;;
	esac
done

# Resolve repo root as the parent of this script's directory.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

INFRA_DIR="$REPO_ROOT/infra"
WEB_DIR="$REPO_ROOT/web"
LOC_TFVARS="locations/${LOCATION}.tfvars"
SECRETS_FILE="${LOCATION}.secrets.tfvars"
SECRETS_PATH="$INFRA_DIR/$SECRETS_FILE"
STATE_BUCKET="funhouse-tfstate-${REGION}"
DOCKERFILE="$REPO_ROOT/funhouse_api/Dockerfile"

# aws CLI common flags.
AWS=(aws --region "$REGION" --profile "$PROFILE")

phase() {
	echo
	printf '=%.0s' $(seq 1 72)
	echo
	echo "  $1"
	printf '=%.0s' $(seq 1 72)
	echo
}
step() { echo "  -> $1"; }
note() { echo "     $1"; }

# ---------------------------------------------------------------------------
# Phase 1 — Preflight
# ---------------------------------------------------------------------------
phase "Phase 1/8: Preflight — tools, credentials, region"
for cmd in aws terraform docker; do
	command -v "$cmd" >/dev/null 2>&1 || {
		echo "Required command '$cmd' not found on PATH." >&2
		exit 1
	}
done
step "aws, terraform, docker present"

docker info >/dev/null 2>&1 || {
	echo "Docker daemon not reachable. Start Docker and retry." >&2
	exit 1
}
step "Docker daemon is running"

step "Verifying AWS credentials"
ACCOUNT="$("${AWS[@]}" sts get-caller-identity --query Account --output text)"
if [ -z "$ACCOUNT" ] || [ "$ACCOUNT" = "None" ]; then
	echo "Could not resolve AWS credentials for profile '$PROFILE'. Run: aws sso login --profile $PROFILE" >&2
	exit 1
fi
ECR_REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_REF="${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG}"
note "Account:      $ACCOUNT"
note "Region:       $REGION"
note "ECR registry: $ECR_REGISTRY"
note "Location:     $LOCATION"

# ---------------------------------------------------------------------------
# Phase 2 — Terraform state bucket (idempotent bootstrap)
# ---------------------------------------------------------------------------
phase "Phase 2/8: Terraform state bucket ($STATE_BUCKET)"
if "${AWS[@]}" s3api head-bucket --bucket "$STATE_BUCKET" >/dev/null 2>&1; then
	step "State bucket already exists — reusing"
else
	step "Creating versioned + encrypted, non-public state bucket"
	"${AWS[@]}" s3api create-bucket --bucket "$STATE_BUCKET" \
		--create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null 2>&1 ||
		"${AWS[@]}" s3api head-bucket --bucket "$STATE_BUCKET" >/dev/null 2>&1 || {
		echo "Failed to create state bucket $STATE_BUCKET." >&2
		exit 1
	}
	"${AWS[@]}" s3api put-bucket-versioning --bucket "$STATE_BUCKET" \
		--versioning-configuration Status=Enabled
	"${AWS[@]}" s3api put-bucket-encryption --bucket "$STATE_BUCKET" \
		--server-side-encryption-configuration \
		'{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
	"${AWS[@]}" s3api put-public-access-block --bucket "$STATE_BUCKET" \
		--public-access-block-configuration \
		BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
	step "State bucket ready"
fi

# ---------------------------------------------------------------------------
# Phase 3 — Secrets (generate once, never overwrite)
# ---------------------------------------------------------------------------
phase "Phase 3/8: Secrets ($SECRETS_FILE)"
if [ -f "$SECRETS_PATH" ]; then
	step "Secrets file already present — leaving untouched"
else
	step "Generating $SECRETS_FILE with random values"
	gen() { openssl rand -hex 32 2>/dev/null || head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
	DB_PASSWORD="$(gen)"
	JWT_SECRET="$(gen)$(gen)"
	{
		echo '# GENERATED by scripts/deploy.sh — DO NOT COMMIT (*.secrets.tfvars is gitignored).'
		echo 'db_master_username = "funhouse_admin"'
		echo "db_master_password = \"$DB_PASSWORD\""
		echo "jwt_secret         = \"$JWT_SECRET\""
	} >"$SECRETS_PATH"
	chmod 600 "$SECRETS_PATH"
	note "Wrote $SECRETS_PATH (stays local; SSM SecureStrings created by terraform apply)."
fi

# ---------------------------------------------------------------------------
# Phase 4 — ECR build + push
# ---------------------------------------------------------------------------
phase "Phase 4/8: ECR — build and push $IMAGE_REF"
if "${AWS[@]}" ecr describe-repositories --repository-names "$ECR_REPO" >/dev/null 2>&1; then
	step "ECR repository '$ECR_REPO' exists — reusing"
else
	step "Creating ECR repository '$ECR_REPO'"
	"${AWS[@]}" ecr create-repository --repository-name "$ECR_REPO" \
		--image-scanning-configuration scanOnPush=true >/dev/null
fi

step "Logging Docker in to ECR"
"${AWS[@]}" ecr get-login-password | docker login --username AWS --password-stdin "$ECR_REGISTRY"

step "Building image (context = repo root)"
docker build -f "$DOCKERFILE" -t "${ECR_REPO}:${IMAGE_TAG}" "$REPO_ROOT"

step "Tagging + pushing to ECR"
docker tag "${ECR_REPO}:${IMAGE_TAG}" "$IMAGE_REF"
docker push "$IMAGE_REF"
step "Image published: $IMAGE_REF"

# ---------------------------------------------------------------------------
# Phase 5 — Terraform apply (phase 1): infra + migrate/seed on start
# ---------------------------------------------------------------------------
phase "Phase 5/8: Terraform apply (infra; migrate+seed on container start)"
step "terraform init"
terraform -chdir="$INFRA_DIR" init -input=false

step "terraform apply (run_migrations_on_start=true, run_seed_on_start=true)"
terraform -chdir="$INFRA_DIR" apply -input=false -auto-approve \
	-var-file="$LOC_TFVARS" -var-file="$SECRETS_FILE" \
	-var run_migrations_on_start=true \
	-var run_seed_on_start=true

APPRUNNER_URL="$(terraform -chdir="$INFRA_DIR" output -raw apprunner_url)"
CLOUDFRONT_DOMAIN="$(terraform -chdir="$INFRA_DIR" output -raw cloudfront_domain)"
DISTRIBUTION_ID="$(terraform -chdir="$INFRA_DIR" output -raw cloudfront_distribution_id)"
WEB_BUCKET="$(terraform -chdir="$INFRA_DIR" output -raw web_bucket_name)"
note "apprunner_url:              $APPRUNNER_URL"
note "cloudfront_domain:          $CLOUDFRONT_DOMAIN"
note "cloudfront_distribution_id: $DISTRIBUTION_ID"
note "web_bucket_name:            $WEB_BUCKET"

# ---------------------------------------------------------------------------
# Phase 6 — Build + publish the Revenue PWA
# ---------------------------------------------------------------------------
phase "Phase 6/8: Build + publish the Revenue PWA"
if command -v npm >/dev/null 2>&1; then
	step "npm found — building PWA against $APPRUNNER_URL"
	# The client reads the API base URL from VITE_API_BASE_URL
	# (web/src/state/authState.tsx -> import.meta.env.VITE_API_BASE_URL).
	(
		cd "$WEB_DIR"
		VITE_API_BASE_URL="$APPRUNNER_URL" npm ci
		VITE_API_BASE_URL="$APPRUNNER_URL" npm run build
	)
	step "Syncing web/dist to s3://$WEB_BUCKET/ (--delete)"
	"${AWS[@]}" s3 sync "$WEB_DIR/dist" "s3://$WEB_BUCKET/" --delete
	step "Invalidating CloudFront (/index.html, /sw.js)"
	"${AWS[@]}" cloudfront create-invalidation \
		--distribution-id "$DISTRIBUTION_ID" --paths '/index.html' '/sw.js' >/dev/null
	step "PWA published"
else
	note "npm not found on PATH — skipping the PWA build (deploy continues)."
	note "Install Node.js LTS then run manually:"
	note "  cd web"
	note "  VITE_API_BASE_URL='$APPRUNNER_URL' npm ci && VITE_API_BASE_URL='$APPRUNNER_URL' npm run build"
	note "  aws --region $REGION --profile $PROFILE s3 sync dist s3://$WEB_BUCKET/ --delete"
	note "  aws --region $REGION --profile $PROFILE cloudfront create-invalidation --distribution-id $DISTRIBUTION_ID --paths /index.html /sw.js"
fi

# ---------------------------------------------------------------------------
# Phase 7 — Terraform apply (phase 2): CORS
# ---------------------------------------------------------------------------
phase "Phase 7/8: Terraform apply (CORS — allow the PWA origin)"
CORS_ORIGIN="https://$CLOUDFRONT_DOMAIN"
step "terraform apply cors_origins=$CORS_ORIGIN"
terraform -chdir="$INFRA_DIR" apply -input=false -auto-approve \
	-var-file="$LOC_TFVARS" -var-file="$SECRETS_FILE" \
	-var run_migrations_on_start=true \
	-var run_seed_on_start=true \
	-var "cors_origins=$CORS_ORIGIN"
step "CORS wired: API now allows $CORS_ORIGIN"

# ---------------------------------------------------------------------------
# Phase 8 — Done
# ---------------------------------------------------------------------------
phase "Phase 8/8: Deployment complete"
echo
echo "  PWA (open this):  https://$CLOUDFRONT_DOMAIN"
echo "  API:              $APPRUNNER_URL"
echo
echo "  next: run the smoke-test checklist -> docs/smoke-test-checklist.md"
echo
