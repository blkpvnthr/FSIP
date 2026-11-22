variable "bucket_name" {
  description = "Globally unique S3 bucket name for this environment. Convention: fsip-<account_or_suffix>-<env> (e.g. fsip-123456789012-dev). Do NOT reuse an existing production bucket name."
  type        = string
}

variable "environment" {
  description = "Deployment environment short name (e.g. dev, staging, prod). Used for tagging."
  type        = string
}

variable "kms_key_arn" {
  description = "Optional KMS key ARN for SSE-KMS encryption. When empty (default), the bucket uses SSE-S3 (AES256). This module never creates a KMS key; supply an existing one if KMS is desired."
  type        = string
  default     = ""
}

variable "abort_multipart_days" {
  description = "Number of days after which incomplete multipart uploads are aborted and their parts deleted."
  type        = number
  default     = 7
}

variable "staging_expiration_days" {
  description = "Number of days after which objects under the staging/ prefix are expired (deleted). Staging is treated as ephemeral scratch space."
  type        = number
  default     = 7
}

variable "noncurrent_version_days" {
  description = "Number of days after which noncurrent (superseded) object versions are permanently expired."
  type        = number
  default     = 30
}

variable "enable_access_logging" {
  description = "When true, enable S3 server access logging to logging_target_bucket. Access logging and its target bucket can add storage and request cost."
  type        = bool
  default     = false
}

variable "logging_target_bucket" {
  description = "Name of an existing S3 bucket that receives server access logs. Required when enable_access_logging is true; ignored otherwise. This module does not create the logging target bucket."
  type        = string
  default     = ""
}

variable "logging_target_prefix" {
  description = "Key prefix within logging_target_bucket under which access logs are written."
  type        = string
  default     = "s3-access-logs/"
}

variable "tags" {
  description = "Additional tags merged onto all resources created by this module. Defaults for Project, ManagedBy, and Environment are applied automatically."
  type        = map(string)
  default     = {}
}
