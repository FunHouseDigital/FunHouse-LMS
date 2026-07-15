# -----------------------------------------------------------------------------
# C5. Static hosting — private S3 + CloudFront (Design C5, D4, D5)
# -----------------------------------------------------------------------------
# A private S3 bucket (Block Public Access on, SSE-S3, af-south-1) holds
# web/dist/. CloudFront serves it over HTTPS with redirect-to-HTTPS, reaching
# the origin only through an Origin Access Control (OAC). SPA deep links fall
# back to /index.html (403/404 -> 200). Hashed assets are cached long; sw.js
# and index.html are no-cache so PWA updates propagate (Req 6).

# AWS-managed CloudFront cache policies (well-known IDs).
locals {
  cache_optimized_id = "658327ea-f89d-4fab-a63d-7e88639e58f6" # CachingOptimized
  cache_disabled_id  = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
}

resource "aws_s3_bucket" "web" {
  bucket_prefix = "funhouse-${var.location_slug}-web-"

  tags = {
    Name = "funhouse-${var.location_slug}-web"
  }
}

# Private: block all public access (Req 6, served only via CloudFront OAC).
resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # SSE-S3
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Origin Access Control — CloudFront signs requests to the private origin.
resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "funhouse-${var.location_slug}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "funhouse-${var.location_slug} PWA"
  # PriceClass_100 keeps edge footprint modest; the S3 origin (Data_At_Rest)
  # stays in af-south-1 (Design D5).
  price_class = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3-web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  # Default behavior: long-lived caching for hashed, content-addressed assets.
  default_cache_behavior {
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https" # Req 6.2, 6.5
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = local.cache_optimized_id
    compress               = true
  }

  # index.html — no-cache so a new deploy is picked up immediately.
  ordered_cache_behavior {
    path_pattern           = "/index.html"
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = local.cache_disabled_id
    compress               = true
  }

  # sw.js — no-cache so the service worker update check always hits origin.
  ordered_cache_behavior {
    path_pattern           = "/sw.js"
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = local.cache_disabled_id
    compress               = true
  }

  # SPA fallback: client-side routes resolve to index.html (Design C5).
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Default CloudFront *.cloudfront.net domain + its managed certificate (D4).
  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Name = "funhouse-${var.location_slug}-cf"
  }
}

# Bucket policy: allow read ONLY from this CloudFront distribution via OAC.
data "aws_iam_policy_document" "web_oac" {
  statement {
    sid       = "AllowCloudFrontOACRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_oac.json

  depends_on = [aws_s3_bucket_public_access_block.web]
}
