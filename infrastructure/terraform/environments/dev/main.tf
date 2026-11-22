provider "aws" {
  region = var.region

  # Credentials come from the standard AWS provider chain (env vars, shared
  # config/credentials files, SSO, or an instance/role profile). Never hardcode
  # or commit access keys.

  default_tags {
    tags = {
      Project     = "FSIP"
      ManagedBy   = "Terraform"
      Environment = var.environment
    }
  }
}

module "s3" {
  source = "../../modules/s3"

  bucket_name             = var.bucket_name
  environment             = var.environment
  kms_key_arn             = var.kms_key_arn
  abort_multipart_days    = var.abort_multipart_days
  staging_expiration_days = var.staging_expiration_days
  noncurrent_version_days = var.noncurrent_version_days
  enable_access_logging   = var.enable_access_logging
  logging_target_bucket   = var.logging_target_bucket
  tags                    = var.tags
}

module "iam" {
  source = "../../modules/iam"

  bucket_arn              = module.s3.bucket_arn
  environment             = var.environment
  create_roles            = var.create_roles
  trusted_principal_arns  = var.trusted_principal_arns
  allow_quarantine_delete = var.allow_quarantine_delete
  tags                    = var.tags
}

module "monitoring" {
  source = "../../modules/monitoring"

  environment              = var.environment
  enable_budget            = var.enable_budget
  monthly_budget_usd       = var.monthly_budget_usd
  budget_escalation_usd    = var.budget_escalation_usd
  alert_emails             = var.alert_emails
  enable_cloudwatch_alarms = var.enable_cloudwatch_alarms
  bucket_name              = module.s3.bucket_name
  tags                     = var.tags
}
