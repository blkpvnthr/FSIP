locals {
  default_tags = {
    Project     = "FSIP"
    ManagedBy   = "Terraform"
    Environment = var.environment
  }

  tags = merge(local.default_tags, var.tags)
}

# ---------------------------------------------------------------------------
# Monthly cost budget (free/low-cost). Notifications at 80% and 100% of the
# budget, plus an absolute-value escalation notification.
# ---------------------------------------------------------------------------
resource "aws_budgets_budget" "monthly_cost" {
  count = var.enable_budget ? 1 : 0

  name         = "${var.name_prefix}-${var.environment}-monthly-cost"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Notify at 80% of the monthly budget (actual spend).
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_emails
  }

  # Notify at 100% of the monthly budget (actual spend).
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_emails
  }

  # Escalation: notify when forecasted spend reaches the absolute escalation amount.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.budget_escalation_usd
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
  }
}

# ---------------------------------------------------------------------------
# Optional CloudWatch alarm on bucket size. Disabled by default to avoid cost.
# BucketSizeBytes is a daily storage metric (free), but alarms are billable.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "bucket_size" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-${var.environment}-bucket-size"
  alarm_description   = "Alarms when the data lake bucket exceeds ${var.bucket_size_alarm_bytes} bytes."
  namespace           = "AWS/S3"
  metric_name         = "BucketSizeBytes"
  statistic           = "Average"
  period              = 86400 # 1 day, matching S3 daily storage metrics
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.bucket_size_alarm_bytes
  treat_missing_data  = "notBreaching"

  dimensions = {
    BucketName  = var.bucket_name
    StorageType = "StandardStorage"
  }

  tags = local.tags
}
