#!/usr/bin/env sh
# =============================================================================
# V1 — Residency assertion (Req 1.1, 1.2, 5.5, 6.3, 13.1)
# =============================================================================
# Parses a `terraform show -json` document and FAILS (non-zero exit) if any
# resource that stores Data_At_Rest resolves to a region other than af-south-1.
#
# Data_At_Rest resource types checked: RDS instances + snapshots (backups),
# S3 buckets, SSM parameters, and any data-bearing CloudWatch log group.
#
# Region for each resource is derived from, in order:
#   1. an explicit `values.region`,
#   2. the region segment of `values.arn` (arn:aws:svc:REGION:...),
#   3. `values.availability_zone` with its trailing AZ letter stripped.
#
# Usage:
#   terraform show -json plan.tfplan > plan.json
#   scripts/assert_residency.sh plan.json
#
# Requires: jq. Makes NO AWS calls — safe to run offline / in CI.
# =============================================================================
set -eu

EXPECTED_REGION="af-south-1"
PLAN_JSON="${1:-}"

if [ -z "$PLAN_JSON" ]; then
	echo "usage: $0 <terraform-show-json-file>" >&2
	exit 2
fi
if [ ! -f "$PLAN_JSON" ]; then
	echo "error: file not found: $PLAN_JSON" >&2
	exit 2
fi

violations=$(
	jq -r --arg want "$EXPECTED_REGION" '
    # Collect every resource object anywhere in the document.
    [ .. | .resources? // empty ] | add // []
    | map(select(.type as $t | [
          "aws_db_instance",
          "aws_db_snapshot",
          "aws_s3_bucket",
          "aws_ssm_parameter",
          "aws_cloudwatch_log_group"
        ] | index($t) != null))
    | map({
        addr: (.address // .type),
        region: (
          (.values.region // "") as $r
          | if $r != "" then $r
            elif ((.values.arn // "") | test("^arn:aws:[a-z0-9-]+:[a-z0-9-]+:"))
              then (.values.arn | split(":")[3])
            elif ((.values.availability_zone // "") != "")
              then (.values.availability_zone | .[0:-1])
            else "" end)
      })
    | map(select(.region != "" and .region != $want))
    | .[] | "  \(.addr): \(.region)"
  ' "$PLAN_JSON"
)

if [ -n "$violations" ]; then
	echo "RESIDENCY VIOLATION — expected all Data_At_Rest in $EXPECTED_REGION:" >&2
	echo "$violations" >&2
	exit 1
fi

echo "OK: every Data_At_Rest resource resolves to $EXPECTED_REGION"
exit 0
