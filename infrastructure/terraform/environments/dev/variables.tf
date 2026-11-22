variable "region" {
  description = "AWS region for all resources in the dev environment."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment short name."
  type        = string
  default     = "dev"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name. Convention: fsip-<account_or_suffix>-dev. No default — you must supply this (see terraform.tfvars.example)."
  type        = string
}

variable "kms_key_arn" {
  description = "Optional existing KMS key ARN for SSE-KMS. Empty (default) uses SSE-S3 (AES256). No KMS key is created."
  type        = string
  default     = ""
}

variable "abort_multipart_days" {
  description = "Days after which incomplete multipart uploads are aborted."
  type        = number
  default     = 7
}

variable "staging_expiration_days" {
  description = "Days after which objects under staging/ are expired."
  type        = number
  default     = 7
}

variable "noncurrent_version_days" {
  description = "Days after which noncurrent object versions are expired."
  type        = number
  default     = 30
}

variable "enable_access_logging" {
  description = "Enable S3 server access logging (adds cost). Requires logging_target_bucket."
  type        = bool
  default     = false
}

variable "logging_target_bucket" {
  description = "Existing bucket to receive access logs when enable_access_logging is true."
  type        = string
  default     = ""
}

variable "create_roles" {
  description = "Create IAM roles (with assume-role trust) in addition to policies. No IAM users are created."
  type        = bool
  default     = true
}

variable "trusted_principal_arns" {
  description = "Principal ARNs allowed to assume the created roles (required when create_roles is true)."
  type        = list(string)
  default     = []
}

variable "allow_quarantine_delete" {
  description = "Also allow the ingestion role to delete under quarantine/* (staging/* is always allowed)."
  type        = bool
  default     = false
}

variable "enable_budget" {
  description = "Create the AWS Budgets monthly cost budget (free/low cost)."
  type        = bool
  default     = true
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD; percentage thresholds are measured against this."
  type        = number
  default     = 5
}

variable "budget_escalation_usd" {
  description = "Absolute-value escalation threshold in USD for an additional forecasted-spend notification."
  type        = number
  default     = 10
}

variable "alert_emails" {
  description = "Email addresses for budget and alarm notifications. NOT a secret, but use real inboxes only in your own tfvars."
  type        = list(string)
  default     = []
}

variable "enable_cloudwatch_alarms" {
  description = "Create CloudWatch metric alarms (adds cost). Defaults to false."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags merged onto all resources (Project/ManagedBy/Environment are added automatically)."
  type        = map(string)
  default     = {}
}
