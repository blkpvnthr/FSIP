output "budget_id" {
  description = "ID of the monthly cost budget, or null when enable_budget is false."
  value       = try(aws_budgets_budget.monthly_cost[0].id, null)
}

output "budget_name" {
  description = "Name of the monthly cost budget, or null when enable_budget is false."
  value       = try(aws_budgets_budget.monthly_cost[0].name, null)
}

output "bucket_size_alarm_arn" {
  description = "ARN of the BucketSizeBytes CloudWatch alarm, or null when enable_cloudwatch_alarms is false."
  value       = try(aws_cloudwatch_metric_alarm.bucket_size[0].arn, null)
}
