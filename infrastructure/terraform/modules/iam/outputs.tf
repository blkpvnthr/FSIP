output "ingestion_policy_arn" {
  description = "ARN of the ingestion/publication IAM policy."
  value       = aws_iam_policy.ingestion.arn
}

output "readonly_policy_arn" {
  description = "ARN of the read-only analytical IAM policy."
  value       = aws_iam_policy.readonly.arn
}

output "infra_admin_policy_arn" {
  description = "ARN of the infrastructure-admin IAM policy (bucket-scoped)."
  value       = aws_iam_policy.infra_admin.arn
}

output "ingestion_role_arn" {
  description = "ARN of the ingestion role, or null when create_roles is false."
  value       = try(aws_iam_role.ingestion[0].arn, null)
}

output "ingestion_role_name" {
  description = "Name of the ingestion role, or null when create_roles is false."
  value       = try(aws_iam_role.ingestion[0].name, null)
}

output "readonly_role_arn" {
  description = "ARN of the read-only role, or null when create_roles is false."
  value       = try(aws_iam_role.readonly[0].arn, null)
}

output "readonly_role_name" {
  description = "Name of the read-only role, or null when create_roles is false."
  value       = try(aws_iam_role.readonly[0].name, null)
}

output "infra_admin_role_arn" {
  description = "ARN of the infrastructure-admin role, or null when create_roles is false."
  value       = try(aws_iam_role.infra_admin[0].arn, null)
}

output "infra_admin_role_name" {
  description = "Name of the infrastructure-admin role, or null when create_roles is false."
  value       = try(aws_iam_role.infra_admin[0].name, null)
}
