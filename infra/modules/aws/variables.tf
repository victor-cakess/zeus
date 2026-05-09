variable "region" {
  type        = string
  description = "AWS region."
}

variable "bucket_name" {
  type        = string
  description = "Name of the S3 data bucket."
}

variable "eia_lookback_days" {
  type        = number
  description = "How many days back the EIA Lambda re-fetches on each run."
  default     = 7
}

variable "eia_schedule_cron" {
  type        = string
  description = "EventBridge cron expression for the EIA daily Step Function trigger."
  default     = "cron(0 7 * * ? *)"
}
