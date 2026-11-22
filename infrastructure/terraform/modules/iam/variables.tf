variable "bucket_arn" {
  description = "ARN of the S3 bucket these policies grant access to. All statements are scoped to this bucket and its objects only."
  type        = string
}

variable "environment" {
  description = "Deployment environment short name (e.g. dev, staging, prod). Used in resource names and tags."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for IAM policy and role names to keep them unique and identifiable (e.g. fsip)."
  type        = string
  default     = "fsip"
}

variable "create_roles" {
  description = "When true, create IAM roles (with assume-role trust) and attach the policies. When false, only the managed policies are created so they can be attached out-of-band. No IAM users are ever created."
  type        = bool
  default     = true
}

variable "trusted_principal_arns" {
  description = "List of IAM principal ARNs (e.g. account root arn:aws:iam::<account_id>:root or specific role ARNs) allowed to assume the created roles. Required when create_roles is true."
  type        = list(string)
  default     = []
}

variable "allow_quarantine_delete" {
  description = "When true, the ingestion policy also permits DeleteObject under quarantine/* (in addition to staging/*). Immutable prefixes remain protected regardless."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags merged onto all IAM resources created by this module."
  type        = map(string)
  default     = {}
}
