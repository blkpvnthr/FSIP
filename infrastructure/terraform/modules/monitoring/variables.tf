variable "environment" {
  description = "Deployment environment short name (e.g. dev, staging, prod). Used in resource names and tags."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for monitoring resource names (e.g. fsip)."
  type        = string
  default     = "fsip"
}

variable "enable_budget" {
  description = "When true, create an AWS Budgets monthly cost budget. AWS Budgets itself is free (first two budgets) or very low cost, so this defaults to true."
  type        = bool
  default     = true
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD. Notifications fire at the configured percentage thresholds of this amount."
  type        = number
  default     = 5
}

variable "budget_escalation_usd" {
  description = "Higher escalation threshold in USD. An absolute-value notification fires when forecasted/actual spend reaches this amount, independent of the percentage thresholds."
  type        = number
  default     = 10
}

variable "alert_emails" {
  description = "List of email addresses that receive budget and alarm notifications. Leave empty to create resources without email subscribers."
  type        = list(string)
  default     = []
}

variable "enable_cloudwatch_alarms" {
  description = "When true, create CloudWatch metric alarms (e.g. S3 BucketSizeBytes). CloudWatch alarms and S3 request metrics can add cost, so this defaults to false."
  type        = bool
  default     = false
}

variable "bucket_name" {
  description = "Name of the S3 bucket to monitor with CloudWatch alarms. Required only when enable_cloudwatch_alarms is true."
  type        = string
  default     = ""
}

variable "bucket_size_alarm_bytes" {
  description = "Threshold in bytes for the BucketSizeBytes CloudWatch alarm. Only used when enable_cloudwatch_alarms is true."
  type        = number
  default     = 53687091200 # 50 GiB
}

variable "tags" {
  description = "Additional tags merged onto monitoring resources that support tagging."
  type        = map(string)
  default     = {}
}
