#!/usr/bin/env sh
# =============================================================================
# V3 — No-forbidden-services assertion (Req 12.1, 12.2, 12.3)
# =============================================================================
# Parses a `terraform show -json` document and FAILS (non-zero exit) if the plan
# contains any resource outside PRD Section 3.1:
#   * load balancers      — aws_lb / aws_alb / aws_elb / target groups / listeners
#   * Kubernetes / EKS     — aws_eks_*
#   * ECS                   — aws_ecs_*
#   * monitoring beyond CloudWatch defaults — aws_cloudwatch_metric_alarm,
#     aws_cloudwatch_composite_alarm, aws_cloudwatch_dashboard
#
# The allowed set is {RDS, App Runner, S3, CloudFront, SSM} plus the VPC / IAM /
# KMS-data / ECR primitives they require. Anything matching the forbidden
# patterns above trips the assertion.
#
# Usage:
#   terraform show -json plan.tfplan > plan.json
#   scripts/assert_no_forbidden.sh plan.json
#
# Requires: jq. Makes NO AWS calls — safe to run offline / in CI.
# =============================================================================
set -eu

PLAN_JSON="${1:-}"

if [ -z "$PLAN_JSON" ]; then
	echo "usage: $0 <terraform-show-json-file>" >&2
	exit 2
fi
if [ ! -f "$PLAN_JSON" ]; then
	echo "error: file not found: $PLAN_JSON" >&2
	exit 2
fi

forbidden=$(
	jq -r '
    [ .. | .resources? // empty ] | add // []
    | map(select(
        (.type | test("^aws_(lb|alb|elb)(_|$)"))
        or (.type | test("^aws_lb_"))
        or (.type | test("^aws_elb"))
        or (.type | startswith("aws_eks"))
        or (.type | startswith("aws_ecs"))
        or (.type == "aws_cloudwatch_metric_alarm")
        or (.type == "aws_cloudwatch_composite_alarm")
        or (.type == "aws_cloudwatch_dashboard")
      ))
    | map("  \(.address // .type)  [\(.type)]")
    | unique
    | .[]
  ' "$PLAN_JSON"
)

if [ -n "$forbidden" ]; then
	echo "FORBIDDEN SERVICE(S) in plan — only PRD Section 3.1 components are allowed:" >&2
	echo "$forbidden" >&2
	exit 1
fi

echo "OK: no forbidden services (no LB/EKS/ECS/CloudWatch alarms) in the plan"
exit 0
