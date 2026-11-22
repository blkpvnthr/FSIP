output "bucket_name" {
  description = "Name of the data lake S3 bucket."
  value       = module.s3.bucket_name
}

output "bucket_arn" {
  description = "ARN of the data lake S3 bucket."
  value       = module.s3.bucket_arn
}

output "ingestion_policy_arn" {
  description = "ARN of the ingestion/publication IAM policy."
  value       = module.iam.ingestion_policy_arn
}

output "readonly_policy_arn" {
  description = "ARN of the read-only analytical IAM policy."
  value       = module.iam.readonly_policy_arn
}

output "infra_admin_policy_arn" {
  description = "ARN of the infrastructure-admin IAM policy."
  value       = module.iam.infra_admin_policy_arn
}

output "ingestion_role_arn" {
  description = "ARN of the ingestion role (null when create_roles is false)."
  value       = module.iam.ingestion_role_arn
}

output "readonly_role_arn" {
  description = "ARN of the read-only role (null when create_roles is false)."
  value       = module.iam.readonly_role_arn
}

output "infra_admin_role_arn" {
  description = "ARN of the infrastructure-admin role (null when create_roles is false)."
  value       = module.iam.infra_admin_role_arn
}

output "budget_name" {
  description = "Name of the monthly cost budget (null when enable_budget is false)."
  value       = module.monitoring.budget_name
}
