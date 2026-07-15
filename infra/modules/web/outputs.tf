output "bucket_name" {
  description = "Name of the private S3 origin bucket."
  value       = aws_s3_bucket.web.id
}

output "bucket_arn" {
  description = "ARN of the S3 origin bucket."
  value       = aws_s3_bucket.web.arn
}

output "cloudfront_domain" {
  description = "CloudFront default *.cloudfront.net domain serving the PWA."
  value       = aws_cloudfront_distribution.web.domain_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (for invalidations)."
  value       = aws_cloudfront_distribution.web.id
}

output "cloudfront_origin" {
  description = "The https origin of the PWA (for API CORS allowlist)."
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}
