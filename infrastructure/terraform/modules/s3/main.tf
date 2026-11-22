locals {
  # Default tags applied to every resource, merged with caller-supplied tags.
  default_tags = {
    Project     = "FSIP"
    ManagedBy   = "Terraform"
    Environment = var.environment
  }

  tags = merge(local.default_tags, var.tags)

  # When a KMS key ARN is supplied, use SSE-KMS; otherwise fall back to SSE-S3 (AES256).
  use_kms = var.kms_key_arn != ""
}

resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name
  tags   = local.tags

  # Guard rail: Terraform will refuse to destroy this bucket. This blocks
  # `terraform destroy` of the data lake bucket. See README for the removal procedure.
  lifecycle {
    prevent_destroy = true
  }
}

# Block ALL forms of public access at the bucket level.
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Disable ACLs entirely; the bucket owner owns all objects.
resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Default server-side encryption. SSE-S3 (AES256) unless a KMS key ARN is provided.
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = local.use_kms ? "aws:kms" : "AES256"
      kms_master_key_id = local.use_kms ? var.kms_key_arn : null
    }

    # Bucket keys reduce KMS request costs; only meaningful for SSE-KMS.
    bucket_key_enabled = local.use_kms
  }
}

# Keep every version of every object so writes are non-destructive.
resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Bucket policy: enforce TLS and enforce that every PutObject is encrypted.
data "aws_iam_policy_document" "bucket" {
  # 1) Deny any request not sent over TLS.
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.this.arn,
      "${aws_s3_bucket.this.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # 2) Deny PutObject when the server-side encryption header is missing.
  statement {
    sid       = "DenyUnencryptedObjectUploads"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.this.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Null"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["true"]
    }
  }

  # 3) Deny PutObject when the encryption algorithm is not the one this bucket expects.
  statement {
    sid       = "DenyIncorrectEncryptionHeader"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.this.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = [local.use_kms ? "aws:kms" : "AES256"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.bucket.json

  # Ensure public access block is in place before attaching a policy.
  depends_on = [aws_s3_bucket_public_access_block.this]
}

# Lifecycle: abort stale multipart uploads, expire staging objects, expire old versions.
resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  # Versioning must be configured before lifecycle rules that reference versions.
  depends_on = [aws_s3_bucket_versioning.this]

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_multipart_days
    }
  }

  rule {
    id     = "expire-staging-objects"
    status = "Enabled"

    filter {
      prefix = "staging/"
    }

    expiration {
      days = var.staging_expiration_days
    }
  }

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_days
    }
  }
}

# Optional server access logging. Gated so default deployments add no log cost.
resource "aws_s3_bucket_logging" "this" {
  count = var.enable_access_logging ? 1 : 0

  bucket        = aws_s3_bucket.this.id
  target_bucket = var.logging_target_bucket
  target_prefix = var.logging_target_prefix
}
