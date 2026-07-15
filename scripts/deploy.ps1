<#
.SYNOPSIS
    One-command automated deploy for the FunHouse Operating System (Spec 3.5).

.DESCRIPTION
    Stands up (or updates) the entire FunHouse stack on AWS af-south-1 from a
    Windows workstation with a single command. It automates the manual sequence
    in docs/deployment-runbook.md:

        preflight -> tfstate bucket -> secrets -> ECR build/push ->
        terraform apply (phase 1, migrate+seed on start) -> PWA build/publish ->
        terraform apply (phase 2, CORS) -> done

    The container entrypoint runs the idempotent schema migration and reference
    seed itself on first boot (RUN_MIGRATIONS_ON_START / RUN_SEED_ON_START set by
    this script), so the manual in-VPC one-off migration step is not required.

    The script is fail-fast and idempotent / re-runnable: existing resources are
    detected and reused, and secret material is generated only once (never
    overwritten). No secret or Terraform state is ever committed.

.PREREQUISITES
    - AWS CLI v2, Terraform >= 1.10, Docker Desktop (running) on PATH.
    - Node.js + npm on PATH (for the PWA build step; optional — if absent the
      script prints the manual PWA steps and continues).
    - Authenticated AWS session for af-south-1, e.g.:  aws sso login --profile funhouse

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/deploy.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/deploy.ps1 -Location loc1 -Profile funhouse

.NOTES
    Next step after a successful run: docs/smoke-test-checklist.md
#>

[CmdletBinding()]
param(
    [string]$Location = 'loc1',
    [string]$Region   = 'af-south-1',
    [string]$Profile  = 'funhouse',
    # Discovered from the caller identity when left empty (never hardcoded).
    [string]$Account  = '',
    [string]$EcrRepo  = 'funhouse-api',
    [string]$ImageTag = 'latest'
)

# ---------------------------------------------------------------------------
# Fail fast: any error or non-zero native exit code aborts the whole deploy.
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Resolve repo root as the parent of this script's directory, so the script can
# be launched from anywhere.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

# AWS CLI flags reused on every call (region + profile).
$AwsCommon = @('--region', $Region, '--profile', $Profile)

function Write-Phase {
    param([string]$Message)
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor Cyan
}

function Write-Step { param([string]$Message) Write-Host "  -> $Message" -ForegroundColor Green }
function Write-Note { param([string]$Message) Write-Host "     $Message" -ForegroundColor DarkGray }

# Assert an external command exists on PATH.
function Assert-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH. $Hint"
    }
}

# Run a native command and throw if it returns a non-zero exit code. Native
# tools (aws/terraform/docker) don't trip $ErrorActionPreference on their own.
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$File,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $File $($Arguments -join ' ')"
    }
}

# ===========================================================================
# Phase 1 — Preflight
# ===========================================================================
Write-Phase "Phase 1/8: Preflight — tools, credentials, region"

Assert-Command -Name 'aws'       -Hint 'Install AWS CLI v2.'
Assert-Command -Name 'terraform' -Hint 'Install Terraform >= 1.10.'
Assert-Command -Name 'docker'    -Hint 'Install Docker Desktop.'
Write-Step "aws, terraform, docker present"

# Docker must be running (daemon reachable).
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed but the daemon is not reachable. Start Docker Desktop and retry."
}
Write-Step "Docker daemon is running"

# Verify credentials resolve; derive the account id if not supplied.
Write-Step "Verifying AWS credentials (aws sts get-caller-identity)"
$callerAccount = (& aws @AwsCommon sts get-caller-identity --query 'Account' --output text)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($callerAccount)) {
    throw "Could not resolve AWS credentials for profile '$Profile'. Run: aws sso login --profile $Profile"
}
if ([string]::IsNullOrWhiteSpace($Account)) {
    $Account = $callerAccount.Trim()
} elseif ($Account -ne $callerAccount.Trim()) {
    Write-Note "WARNING: -Account '$Account' differs from the authenticated account '$($callerAccount.Trim())'. Using the authenticated account."
    $Account = $callerAccount.Trim()
}

$EcrRegistry = "$Account.dkr.ecr.$Region.amazonaws.com"
$ImageRef    = "$EcrRegistry/${EcrRepo}:$ImageTag"
Write-Note "Account:      $Account"
Write-Note "Region:       $Region"
Write-Note "ECR registry: $EcrRegistry"
Write-Note "Location:     $Location"

# Paths used throughout.
$InfraDir       = Join-Path $RepoRoot 'infra'
$WebDir         = Join-Path $RepoRoot 'web'
$LocTfvars      = "locations/$Location.tfvars"          # relative to infra (via -chdir)
$SecretsFile    = "$Location.secrets.tfvars"            # relative to infra
$SecretsPath    = Join-Path $InfraDir $SecretsFile
$StateBucket    = "funhouse-tfstate-$Region"
$DockerfilePath = Join-Path $RepoRoot 'funhouse_api/Dockerfile'

# ===========================================================================
# Phase 2 — Terraform remote-state bucket (bootstrap, idempotent)
# ===========================================================================
Write-Phase "Phase 2/8: Terraform state bucket ($StateBucket)"

& aws @AwsCommon s3api head-bucket --bucket $StateBucket *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Step "State bucket already exists — reusing"
} else {
    Write-Step "Creating versioned + encrypted, non-public state bucket"
    # af-south-1 requires an explicit LocationConstraint. AlreadyOwnedByYou is
    # tolerated for re-runnability.
    & aws @AwsCommon s3api create-bucket --bucket $StateBucket `
        --create-bucket-configuration LocationConstraint=$Region *> $null
    if ($LASTEXITCODE -ne 0) {
        # If we already own it (race / prior partial run), continue; else fail.
        & aws @AwsCommon s3api head-bucket --bucket $StateBucket *> $null
        if ($LASTEXITCODE -ne 0) { throw "Failed to create state bucket $StateBucket." }
        Write-Note "Bucket already owned — continuing."
    }
    Invoke-Native aws @AwsCommon s3api put-bucket-versioning --bucket $StateBucket `
        --versioning-configuration Status=Enabled
    Invoke-Native aws @AwsCommon s3api put-bucket-encryption --bucket $StateBucket `
        --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
    Invoke-Native aws @AwsCommon s3api put-public-access-block --bucket $StateBucket `
        --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
    Write-Step "State bucket ready"
}

# ===========================================================================
# Phase 3 — Secrets (generate once, never overwrite, never commit)
# ===========================================================================
Write-Phase "Phase 3/8: Secrets ($SecretsFile)"

if (Test-Path $SecretsPath) {
    Write-Step "Secrets file already present — leaving untouched (never overwritten)"
} else {
    Write-Step "Generating $SecretsFile with random values"
    # GUID-derived random material (concatenated for length). 'N' => 32 hex chars.
    $dbPassword = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
    $jwtSecret  = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
    $lines = @(
        '# GENERATED by scripts/deploy.ps1 — DO NOT COMMIT (matched by *.secrets.tfvars in .gitignore).'
        'db_master_username = "funhouse_admin"'
        "db_master_password = `"$dbPassword`""
        "jwt_secret         = `"$jwtSecret`""
    )
    # ASCII, no BOM — Terraform's HCL parser wants plain text.
    Set-Content -Path $SecretsPath -Value $lines -Encoding ascii
    Write-Note "Wrote $SecretsPath (secrets stay local; SSM SecureStrings are created by terraform apply)."
}

# ===========================================================================
# Phase 4 — ECR: repo + build + push the Container_API image
# ===========================================================================
Write-Phase "Phase 4/8: ECR — build and push $ImageRef"

& aws @AwsCommon ecr describe-repositories --repository-names $EcrRepo *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Step "ECR repository '$EcrRepo' exists — reusing"
} else {
    Write-Step "Creating ECR repository '$EcrRepo'"
    Invoke-Native aws @AwsCommon ecr create-repository --repository-name $EcrRepo `
        --image-scanning-configuration scanOnPush=true *> $null
}

Write-Step "Logging Docker in to ECR"
$pw = (& aws @AwsCommon ecr get-login-password)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pw)) { throw "Failed to obtain ECR login password." }
$pw | & docker login --username AWS --password-stdin $EcrRegistry
if ($LASTEXITCODE -ne 0) { throw "docker login to $EcrRegistry failed." }

Write-Step "Building image (context = repo root)"
Invoke-Native docker build -f $DockerfilePath -t "${EcrRepo}:$ImageTag" $RepoRoot

Write-Step "Tagging + pushing to ECR"
Invoke-Native docker tag "${EcrRepo}:$ImageTag" $ImageRef
Invoke-Native docker push $ImageRef
Write-Step "Image published: $ImageRef"

# ===========================================================================
# Phase 5 — Terraform apply (phase 1): infra + migrate/seed on start
# ===========================================================================
Write-Phase "Phase 5/8: Terraform apply (infra; migrate+seed on container start)"

Write-Step "terraform init"
Invoke-Native terraform "-chdir=$InfraDir" init -input=false

Write-Step "terraform apply (run_migrations_on_start=true, run_seed_on_start=true)"
Invoke-Native terraform "-chdir=$InfraDir" apply -input=false -auto-approve `
    "-var-file=$LocTfvars" "-var-file=$SecretsFile" `
    '-var' 'run_migrations_on_start=true' `
    '-var' 'run_seed_on_start=true'

# Capture outputs needed by the PWA + CORS phases.
function Get-TfOutput {
    param([string]$Name)
    $val = (& terraform "-chdir=$InfraDir" output -raw $Name)
    if ($LASTEXITCODE -ne 0) { throw "Failed to read terraform output '$Name'." }
    return $val.Trim()
}

$ApprunnerUrl    = Get-TfOutput 'apprunner_url'
$CloudFrontDom   = Get-TfOutput 'cloudfront_domain'
$DistributionId  = Get-TfOutput 'cloudfront_distribution_id'
$WebBucket       = Get-TfOutput 'web_bucket_name'
Write-Note "apprunner_url:              $ApprunnerUrl"
Write-Note "cloudfront_domain:          $CloudFrontDom"
Write-Note "cloudfront_distribution_id: $DistributionId"
Write-Note "web_bucket_name:            $WebBucket"

# ===========================================================================
# Phase 6 — Build and publish the Revenue PWA
# ===========================================================================
Write-Phase "Phase 6/8: Build + publish the Revenue PWA"

if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Step "npm found — building PWA against $ApprunnerUrl"
    # The client reads the API base URL from Vite's VITE_API_BASE_URL
    # (web/src/state/authState.tsx -> import.meta.env.VITE_API_BASE_URL).
    $env:VITE_API_BASE_URL = $ApprunnerUrl
    Push-Location $WebDir
    try {
        Invoke-Native npm ci
        Invoke-Native npm run build
    } finally {
        Pop-Location
        Remove-Item Env:\VITE_API_BASE_URL -ErrorAction SilentlyContinue
    }

    $DistDir = Join-Path $WebDir 'dist'
    Write-Step "Syncing web/dist to s3://$WebBucket/ (--delete)"
    Invoke-Native aws @AwsCommon s3 sync $DistDir "s3://$WebBucket/" --delete

    Write-Step "Invalidating CloudFront (/index.html, /sw.js)"
    Invoke-Native aws @AwsCommon cloudfront create-invalidation `
        --distribution-id $DistributionId --paths '/index.html' '/sw.js' *> $null
    Write-Step "PWA published"
} else {
    Write-Note "npm not found on PATH — skipping the PWA build (deploy continues)."
    Write-Note "Install Node.js LTS (https://nodejs.org) then run the following manually:"
    Write-Note "  cd web"
    Write-Note "  `$env:VITE_API_BASE_URL='$ApprunnerUrl'; npm ci; npm run build"
    Write-Note "  aws --region $Region --profile $Profile s3 sync dist s3://$WebBucket/ --delete"
    Write-Note "  aws --region $Region --profile $Profile cloudfront create-invalidation --distribution-id $DistributionId --paths /index.html /sw.js"
}

# ===========================================================================
# Phase 7 — Terraform apply (phase 2): wire CORS to the CloudFront origin
# ===========================================================================
Write-Phase "Phase 7/8: Terraform apply (CORS — allow the PWA origin)"

$CorsOrigin = "https://$CloudFrontDom"
Write-Step "terraform apply cors_origins=$CorsOrigin"
Invoke-Native terraform "-chdir=$InfraDir" apply -input=false -auto-approve `
    "-var-file=$LocTfvars" "-var-file=$SecretsFile" `
    '-var' 'run_migrations_on_start=true' `
    '-var' 'run_seed_on_start=true' `
    '-var' "cors_origins=$CorsOrigin"
Write-Step "CORS wired: API now allows $CorsOrigin"

# ===========================================================================
# Phase 8 — Done
# ===========================================================================
Write-Phase "Phase 8/8: Deployment complete"
Write-Host ''
Write-Host "  PWA (open this):  https://$CloudFrontDom" -ForegroundColor Yellow
Write-Host "  API:              $ApprunnerUrl" -ForegroundColor Yellow
Write-Host ''
Write-Host "  next: run the smoke-test checklist -> docs/smoke-test-checklist.md" -ForegroundColor Cyan
Write-Host ''
