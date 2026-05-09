variable "bucket_arn" {
  type        = string
  description = "ARN of the shared S3 data bucket."
}

variable "bucket_name" {
  type        = string
  description = "Name of the shared S3 data bucket."
}

variable "lookback_days" {
  type        = number
  description = "How many days back the EIA Lambda re-fetches on each run."
}

variable "schedule_cron" {
  type        = string
  description = "EventBridge cron expression for the daily EIA Step Function trigger."
}
